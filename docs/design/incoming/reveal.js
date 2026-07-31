/* reveal.js — 스크롤 등장 모션 (대시보드·랜딩 공용)
 *
 * 요소가 화면에 들어오는 순간 아래에서 떠오르며 나타나고, 한 번 나타난 뒤에는
 * 그 상태로 고정된다. 스크롤을 위아래로 반복해도 다시 재생되지 않는다.
 *
 * 설계 메모
 * - <head> 에서 로드한다. 스크립트가 실행되면 곧바로 <html> 에 reveal-ready 를 붙이고,
 *   CSS 는 그 클래스가 있을 때만 대상을 숨긴다. 스크립트가 없거나 로드에 실패하면
 *   아무것도 숨겨지지 않아 "모션 때문에 화면이 텅 비는" 사고가 나지 않는다.
 * - 시차(stagger)는 CSS 의 nth-child 가 아니라 "같이 화면에 들어온 묶음 안에서의 순서"로
 *   준다. nth-child 로 고정하면 뒤쪽 요소가 혼자 나타날 때 제 순번만큼 쓸데없이 기다린다.
 * - 재생이 끝나면 is-done 을 붙여 애니메이션을 떼어낸다. 안 그러면 대시보드에서 카드를
 *   드래그로 재배치할 때 DOM 이 이동하면서 페이드인이 처음부터 다시 재생된다.
 */
(function () {
  "use strict";

  // 등장 대상 — css/skeleton.css 의 숨김 규칙과 반드시 같게 유지한다.
  var TARGETS = ".grid > .card, .hero-copy > *, .hero-preview, .landing-about > *";

  // CSS 가 대상을 숨겨도 되는 시점 = 이 스크립트가 살아 있음이 확인된 지금부터.
  document.documentElement.classList.add("reveal-ready");

  function reveal(el, delaySec) {
    el.style.animationDelay = delaySec + "s";
    el.classList.add("is-visible");
    el.addEventListener("animationend", function () {
      el.classList.add("is-done");
    }, { once: true });
  }

  function init() {
    var items = document.querySelectorAll(TARGETS);
    if (!items.length) return;

    // 모션 최소화 설정이거나 IntersectionObserver 미지원이면 애니메이션 없이 즉시 표시한다.
    // (이 처리가 없으면 두 경우 모두 요소가 숨겨진 채로 영영 남는다.)
    var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced || !("IntersectionObserver" in window)) {
      Array.prototype.forEach.call(items, function (el) { el.classList.add("is-done"); });
      return;
    }

    var io = new IntersectionObserver(function (entries) {
      entries
        .filter(function (e) { return e.isIntersecting; })
        // 콜백에 담기는 순서가 문서 순서라는 보장이 없어 직접 정렬한다(위→아래, 좌→우).
        .sort(function (a, b) {
          return (a.target.compareDocumentPosition(b.target) & Node.DOCUMENT_POSITION_FOLLOWING)
            ? -1 : 1;
        })
        .forEach(function (e, i) {
          // 시차 값은 theme.css 토큰(--stagger-step). 페이지·영역별로 덮어쓸 수 있다.
          var step = parseFloat(
            getComputedStyle(e.target).getPropertyValue("--stagger-step")) || 0.05;
          reveal(e.target, i * step);
          io.unobserve(e.target);   // 1회만 — 되감기 없음
        });
    }, { rootMargin: "0px 0px -8% 0px" });   // 살짝 올라온 뒤 시작

    Array.prototype.forEach.call(items, function (el) { io.observe(el); });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
