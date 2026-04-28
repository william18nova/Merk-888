(function () {
  "use strict";

  function isFocusedNumberInput(target) {
    return (
      target instanceof HTMLInputElement &&
      target.type === "number" &&
      document.activeElement === target
    );
  }

  document.addEventListener(
    "wheel",
    function (event) {
      if (isFocusedNumberInput(event.target)) {
        event.preventDefault();
      }
    },
    { capture: true, passive: false }
  );
})();
