/* to-top.js — "맨 위로" 버튼 (랜딩·대시보드 공용)
 *
 * 화면 오른쪽 아래에 떠 있는 원형 버튼. 스크롤을 내리기 전에는 보이지 않다가
 * 한 화면 정도 내려가면 나타난다(모양·모션은 css/skeleton.css 의 .to-top).
 *
 * 설계 메모
 * - 숨김은 CSS 기본값이고 이 스크립트는 .is-shown 만 붙였다 뗀다. 스크립트가 없거나
 *   로드에 실패하면 버튼이 계속 숨어 있을 뿐 화면이 깨지지 않는다.
 * - <body> 끝에서 읽는다(reveal.js 처럼 첫 페인트 전에 개입할 일이 없다).
 *   그래도 readyState 를 확인해 <head> 로 옮겨도 동작하게 둔다.
 * - 스크롤 이동은 window.scrollTo 로 한다. 랜딩은 html{scroll-behavior:smooth} 가
 *   있지만 대시보드에는 없어서, CSS 에 기대면 페이지마다 동작이 갈린다.
 */
(function () {
  "use strict";

  // 나타나기 시작하는 스크롤 깊이. 헤더 한 덩어리를 지난 뒤여야 "돌아갈 만큼 내려왔다"가 된다.
  var SHOW_AT = 240;

  function init() {
    var btn = document.querySelector("[data-to-top]");
    if (!btn) return;

    function sync() {
      btn.classList.toggle("is-shown", window.scrollY > SHOW_AT);
    }

    btn.addEventListener("click", function () {
      var reduced = window.matchMedia
        && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      window.scrollTo({ top: 0, behavior: reduced ? "auto" : "smooth" });
    });

    window.addEventListener("scroll", sync, { passive: true });
    window.addEventListener("resize", sync);
    sync();   // 새로고침으로 중간 위치에서 시작하는 경우
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
