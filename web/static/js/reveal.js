/* reveal.js — 스크롤 등장 모션 (스태거 페이드인).
   화면에 들어오는 순간 아래에서 떠오르며 나타나고, 한 번 나타나면 그대로 고정된다
   (스크롤을 위아래로 반복해도 재생되지 않는다).

   역할 분담: 값(시간·거리·곡선)은 theme.css 의 --stagger-* 토큰, 상태별 모양은
   skeleton.css 의 .is-visible / .is-done, 클래스 부착 타이밍만 여기서 정한다.

   ⚠ TARGETS 는 skeleton.css 의 :where(.reveal-ready) ... 선택자와 **반드시 같게** 유지한다.
      한쪽만 고치면 "숨겼는데 아무도 보여주지 않는" 요소가 생겨 화면이 빈다.

   빈 화면 사고 방지가 이 파일의 설계 전제다 — 숨기는 CSS 는 <html> 에 .reveal-ready 가
   붙어야만 작동하고, 그 클래스는 여기서만 붙인다. 스크립트가 404 이거나 실행에 실패하면
   아무것도 숨지 않고 모션만 없는 평범한 화면이 된다.
   외부 CDN 의존 없음(데모데이 오프라인 폴백 원칙). */
(function () {
  "use strict";

  var TARGETS = ".grid > .card, .hero-copy > *, .hero-preview, .landing-about > *";
  var root = document.documentElement;

  // 모션 최소화 설정이거나 IntersectionObserver 가 없으면 아예 개입하지 않는다.
  // (.reveal-ready 를 붙이지 않으므로 요소가 숨겨지지도 않는다.)
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduce || !("IntersectionObserver" in window)) return;

  // <head> 에서 실행된다 — 이 시점엔 <body> 가 아직 없지만 documentElement 는 있다.
  // 여기서 바로 붙여야 카드가 잠깐 보였다 숨는 깜빡임이 없다.
  root.classList.add("reveal-ready");

  // theme.css 의 --stagger-step 을 초 단위로 읽는다(.05s / 50ms 둘 다 허용).
  function seconds(name, fallback) {
    var raw = getComputedStyle(root).getPropertyValue(name).trim();
    var v = parseFloat(raw);
    if (isNaN(v)) return fallback;
    return /ms$/.test(raw) ? v / 1000 : v;
  }

  function start() {
    var items = document.querySelectorAll(TARGETS);
    // 대상이 하나도 없는 페이지(로그인 등)에서는 숨김 규칙을 걷어내고 끝낸다.
    if (!items.length) { root.classList.remove("reveal-ready"); return; }

    var step = seconds("--stagger-step", 0.05);

    var io = new IntersectionObserver(function (entries) {
      // 한 번의 콜백에 함께 들어온 것들이 "같이 나타나는 묶음"이다 —
      // 그 안에서만 시차를 준다(따로 batch 를 계산할 필요가 없다).
      var batch = entries.filter(function (e) { return e.isIntersecting; });
      batch.forEach(function (entry, i) {
        var node = entry.target;
        io.unobserve(node);                       // 한 번 나타나면 다시 재생하지 않는다
        node.style.animationDelay = (i * step).toFixed(3) + "s";
        node.classList.add("is-visible");
      });
    }, { threshold: 0.08 });

    Array.prototype.forEach.call(items, function (node) {
      // 재생이 끝나면 애니메이션을 떼어낸다 — 안 그러면 카드를 드래그로 재배치할 때
      // DOM 이 이동하면서 페이드인이 처음부터 다시 재생된다(skeleton.css .is-done).
      node.addEventListener("animationend", function () {
        node.style.animationDelay = "";
        node.classList.add("is-done");
      }, { once: true });
      io.observe(node);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
