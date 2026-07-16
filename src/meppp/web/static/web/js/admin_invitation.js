(() => {
  const copyButton = document.querySelector("[data-copy-invitation]");
  if (!copyButton) return;

  const tokenInput = document.getElementById(copyButton.dataset.copyInvitation);
  const status = document.getElementById(copyButton.dataset.copyStatus);
  if (!tokenInput || !status) return;

  copyButton.addEventListener("click", async () => {
    try {
      if (!navigator.clipboard || !window.isSecureContext) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(tokenInput.value);
      status.textContent = "邀请码已复制。";
    } catch {
      tokenInput.focus();
      tokenInput.select();
      status.textContent = "邀请码已选中，请使用系统复制命令。";
    }
  });
})();
