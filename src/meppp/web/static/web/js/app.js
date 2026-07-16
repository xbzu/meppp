(() => {
  "use strict";

  const counters = document.querySelectorAll("[data-character-input]");
  counters.forEach((input) => {
    const name = input.dataset.characterInput;
    const output = document.querySelector(`[data-character-count="${name}"]`);
    if (!output) return;

    const update = () => {
      output.textContent = String(input.value.length);
      output.closest("span")?.classList.toggle(
        "is-near-limit",
        Boolean(input.maxLength > 0 && input.value.length >= input.maxLength * 0.9),
      );
    };
    input.addEventListener("input", update);
    update();
  });
})();
