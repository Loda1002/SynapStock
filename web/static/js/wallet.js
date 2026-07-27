// 지갑 연결 = 로그인 (Phantom 등 Solana 지갑).
//
// 이 파일 하나로 연결 페이지(/connect)와 대시보드 헤더가 같은 흐름을 쓴다.
// 서버 계약은 web/auth.py 참조 — 서버가 만든 메시지 전문을 **그대로** 서명해 돌려준다.
// 우리가 문자열을 조립하지 않는 이유: 클라이언트가 한 글자라도 다르게 만들면 서버의
// 완전 일치 검증에서 떨어진다. 정본은 항상 서버에 있다.
//
// DOM 접근은 data-속성 훅만 쓴다(app.js 와 같은 계약) — 디자인 스킨 교체 시 클래스는 자유.
(function (global) {
  "use strict";

  // ---- 지갑 provider 탐지 ----
  // Phantom 은 window.phantom.solana 에 주입한다. 옛 버전·일부 지갑은 window.solana 만 둔다.
  // isPhantom 이 아닌 provider(Solflare 등)도 connect/signMessage 규약이 같아 그대로 동작한다.
  function getProvider() {
    var p = (global.phantom && global.phantom.solana) || global.solana;
    return (p && typeof p.connect === "function" && typeof p.signMessage === "function") ? p : null;
  }

  var PHANTOM_URL = "https://phantom.com/download";

  function bytesToBase64(bytes) {
    var s = "";
    for (var i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
    return btoa(s);
  }

  function shortKey(k) {
    return (k && k.length > 12) ? k.slice(0, 4) + "…" + k.slice(-4) : (k || "");
  }

  var B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";

  function base58ToBytes(str) {
    var bytes = [0];
    for (var i = 0; i < str.length; i++) {
      var v = B58.indexOf(str[i]);
      if (v < 0) return null;                       // base58 이 아니다
      for (var j = 0; j < bytes.length; j++) bytes[j] *= 58;
      bytes[0] += v;
      var carry = 0;
      for (var k = 0; k < bytes.length; k++) {
        bytes[k] += carry; carry = bytes[k] >> 8; bytes[k] &= 0xff;
      }
      while (carry) { bytes.push(carry & 0xff); carry >>= 8; }
    }
    for (var z = 0; z < str.length && str[z] === "1"; z++) bytes.push(0);
    return new Uint8Array(bytes.reverse());
  }

  // 지갑이 돌려주는 서명을 64바이트로 정규화한다.
  //
  // 왜 필요한가: `signMessage` 의 반환 형태가 지갑마다 다르다. Phantom 은
  // `{signature: Uint8Array}` 를 주지만, 어떤 지갑은 Uint8Array 를 그대로 주고, 어떤 지갑은
  // base58/base64 문자열을 주며, 브리지(모바일 인앱 브라우저 등)를 거치면 JSON 직렬화 때문에
  // `{0:12, 1:87, ...}` 같은 평범한 객체로 도착한다. 예전 코드는 `new Uint8Array(값)` 한 줄이라
  // 앞의 둘만 통과하고 나머지는 **길이 0짜리 서명**을 만들어 서버에 보냈다. 그러면 사용자는
  // "서명이 올바르지 않습니다"라는, 원인을 알 수 없는 401 만 보게 된다.
  // 여기서 형태를 흡수하고, 그래도 64바이트가 아니면 무엇을 받았는지 말해 주는 오류를 던진다.
  function toSignatureBytes(signed) {
    var raw = (signed && signed.signature !== undefined) ? signed.signature : signed;
    var out = null;
    if (raw instanceof Uint8Array) out = raw;
    else if (raw instanceof ArrayBuffer) out = new Uint8Array(raw);
    else if (Array.isArray(raw)) out = new Uint8Array(raw);
    else if (typeof raw === "string") {
      out = base58ToBytes(raw);
      if (!out || out.length !== 64) {
        try { out = Uint8Array.from(atob(raw), function (c) { return c.charCodeAt(0); }); }
        catch (e) { /* base64 도 아니다 */ }
      }
    } else if (raw && typeof raw === "object") {
      var keys = Object.keys(raw).filter(function (k) { return /^\d+$/.test(k); });
      if (keys.length) {
        out = new Uint8Array(keys.length);
        keys.forEach(function (k) { out[Number(k)] = raw[k]; });
      }
    }
    if (!out || out.length !== 64) {
      var got = (out ? out.length + "바이트" : typeof raw);
      throw new Error("지갑이 돌려준 서명 형식을 해석하지 못했습니다 (받은 값: " + got +
                      ", ed25519 서명은 64바이트여야 합니다). 지갑 종류와 이 메시지를 알려주세요.");
    }
    return out;
  }

  async function postJSON(url, body) {
    var res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
      credentials: "same-origin",   // 세션 쿠키를 주고받는다
    });
    var data = null;
    try { data = await res.json(); } catch (e) { /* 본문 없는 응답 */ }
    if (!res.ok) {
      var msg = (data && data.detail) ? data.detail : ("요청 실패 (" + res.status + ")");
      throw new Error(msg);
    }
    return data;
  }

  // ---- 연결 흐름: connect → challenge → signMessage → verify ----
  async function connect() {
    var provider = getProvider();
    if (!provider) {
      var e = new Error("이 브라우저에서 Solana 지갑을 찾지 못했습니다. Phantom 확장을 설치한 뒤 새로고침해 주세요.");
      e.code = "NO_PROVIDER";
      throw e;
    }

    // 1) 지갑 연결 — 사용자가 Phantom 팝업에서 승인한다.
    var resp = await provider.connect();
    var pubkey = String((resp && resp.publicKey) || provider.publicKey);
    if (!pubkey || pubkey === "null" || pubkey === "undefined") {
      throw new Error("지갑에서 주소를 받지 못했습니다.");
    }

    // 2) 서버가 서명할 메시지 전문을 만들어 준다(nonce 포함, 1회용).
    var ch = await postJSON("/api/auth/challenge", { pubkey: pubkey });

    // 3) 그 문자열을 그대로 서명한다. 사용자는 Phantom 팝업에서 원문을 읽고 승인한다.
    var encoded = new TextEncoder().encode(ch.message);
    var signed = await provider.signMessage(encoded, "utf8");
    var sigBytes = toSignatureBytes(signed);   // provider 별 반환 형태 차이 흡수 (위 주석)

    // 4) 서버가 원문 일치 + ed25519 서명을 검증하고 세션 쿠키를 심는다.
    await postJSON("/api/auth/verify", {
      pubkey: pubkey,
      message: ch.message,
      signature: bytesToBase64(sigBytes),
    });
    return pubkey;
  }

  async function me() {
    var res = await fetch("/api/auth/me", { credentials: "same-origin" });
    if (!res.ok) return { connected: false, pubkey: "" };
    return await res.json();
  }

  async function logout() {
    await postJSON("/api/auth/logout", {});
    var provider = getProvider();
    if (provider && typeof provider.disconnect === "function") {
      try { await provider.disconnect(); } catch (e) { /* 지갑이 거부해도 서버 세션은 끊겼다 */ }
    }
  }

  // 사용자가 팝업을 닫은 경우(코드 4001)는 오류가 아니라 취소다 — 빨간 문구를 띄우지 않는다.
  function isUserRejection(err) {
    return !!err && (err.code === 4001 || /User rejected|사용자가 거부/i.test(err.message || ""));
  }

  // ---- 대시보드 헤더용 위젯 ----
  // [data-wallet-connect] 버튼 + [data-wallet-status] 텍스트가 있는 페이지에서 자동 배선된다.
  function mountHeader() {
    var btn = document.querySelector("[data-wallet-connect]");
    var status = document.querySelector("[data-wallet-status]");
    if (!btn) return;

    var connected = false;

    function render(pubkey) {
      connected = !!pubkey;
      btn.textContent = connected ? "연결 해제" : "지갑 연결";
      btn.classList.toggle("is-connected", connected);
      if (status) {
        status.textContent = connected ? shortKey(pubkey) : "미연결";
        status.title = connected ? pubkey : "지갑을 연결하면 이 세션의 위임자로 기록됩니다";
      }
    }

    btn.addEventListener("click", async function () {
      btn.disabled = true;
      try {
        if (connected) {
          await logout();
          render("");
        } else {
          render(await connect());
        }
      } catch (err) {
        if (!isUserRejection(err)) {
          if (err.code === "NO_PROVIDER") {
            if (confirm(err.message + "\n\n설치 페이지를 여시겠습니까?")) global.open(PHANTOM_URL, "_blank");
          } else {
            alert("지갑 연결 실패: " + err.message);
          }
        }
      } finally {
        btn.disabled = false;
      }
    });

    me().then(function (s) { render(s.connected ? s.pubkey : ""); }).catch(function () { render(""); });
  }

  global.Wallet402 = {
    getProvider: getProvider, connect: connect, me: me, logout: logout,
    shortKey: shortKey, isUserRejection: isUserRejection, mountHeader: mountHeader,
    PHANTOM_URL: PHANTOM_URL,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mountHeader);
  } else {
    mountHeader();
  }
})(window);
