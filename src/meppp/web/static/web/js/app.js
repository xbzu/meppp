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

  const composer = document.querySelector("[data-image-composer]");
  const imageInput = composer?.querySelector("[data-image-input]");
  const altState = document.querySelector("#id_image_alt_texts");
  const previewList = composer?.querySelector("[data-image-preview-list]");
  const status = composer?.querySelector("[data-image-status]");
  if (!composer || !imageInput || !altState || !previewList || !status) return;

  const maximumImages = Number(imageInput.dataset.maxImages || "0");
  const maximumBytes = Number(imageInput.dataset.maxBytes || "0");
  const allowedTypes = new Set(["image/jpeg", "image/png", "image/webp"]);
  let altTexts = [];
  let objectUrls = [];

  const releaseObjectUrls = () => {
    objectUrls.forEach((url) => URL.revokeObjectURL(url));
    objectUrls = [];
  };

  const syncAltState = () => {
    altState.value = JSON.stringify(altTexts);
  };

  const removeImage = (removedIndex) => {
    const transfer = new DataTransfer();
    Array.from(imageInput.files).forEach((file, index) => {
      if (index !== removedIndex) transfer.items.add(file);
    });
    imageInput.files = transfer.files;
    altTexts.splice(removedIndex, 1);
    renderImages();
  };

  const renderImages = () => {
    releaseObjectUrls();
    previewList.replaceChildren();
    const files = Array.from(imageInput.files);
    altTexts = files.map((_, index) => altTexts[index] || "");
    syncAltState();

    const issues = [];
    if (files.length > maximumImages) issues.push(`最多选择 ${maximumImages} 张`);
    files.forEach((file, index) => {
      if (!allowedTypes.has(file.type)) issues.push(`第 ${index + 1} 张格式不支持`);
      if (file.size > maximumBytes) issues.push(`第 ${index + 1} 张超过大小限制`);

      const item = document.createElement("article");
      item.className = "image-preview-item";
      item.dataset.imagePreviewItem = "";

      const frame = document.createElement("div");
      frame.className = "image-preview-frame";
      if (allowedTypes.has(file.type)) {
        const preview = document.createElement("img");
        const objectUrl = URL.createObjectURL(file);
        objectUrls.push(objectUrl);
        preview.src = objectUrl;
        preview.alt = "";
        preview.dataset.imagePreview = "";
        frame.append(preview);
      }

      const details = document.createElement("div");
      details.className = "image-preview-details";
      const position = document.createElement("span");
      position.className = "image-position";
      position.dataset.imagePosition = "";
      position.textContent = `图片 ${index + 1}`;
      const filename = document.createElement("strong");
      filename.className = "image-filename";
      filename.dataset.imageFilename = "";
      filename.textContent = file.name;

      const label = document.createElement("label");
      const altId = `image-alt-${index + 1}`;
      label.htmlFor = altId;
      label.textContent = `图片 ${index + 1}（${file.name}）的替代文本（选填）`;
      const altInput = document.createElement("input");
      altInput.id = altId;
      altInput.type = "text";
      altInput.maxLength = 240;
      altInput.className = "field-control image-alt-input";
      altInput.dataset.imageAlt = "";
      altInput.value = altTexts[index];
      altInput.placeholder = "简要描述图片内容；装饰图片可留空";
      altInput.addEventListener("input", () => {
        altTexts[index] = altInput.value;
        syncAltState();
      });

      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "button-secondary image-remove";
      remove.dataset.imageRemove = "";
      remove.textContent = "移除";
      remove.setAttribute("aria-label", `移除图片 ${index + 1}：${file.name}`);
      remove.addEventListener("click", () => removeImage(index));

      details.append(position, filename, label, altInput, remove);
      item.append(frame, details);
      previewList.append(item);
    });

    status.textContent = files.length
      ? issues.length
        ? `已选择 ${files.length} / ${maximumImages} 张；${issues.join("；")}`
        : `已选择 ${files.length} / ${maximumImages} 张`
      : "还没有选择图片";
    status.classList.toggle("has-error", issues.length > 0);
  };

  imageInput.addEventListener("change", () => {
    altTexts = Array.from(imageInput.files, () => "");
    renderImages();
  });
  window.addEventListener("pagehide", releaseObjectUrls, { once: true });
  renderImages();
})();
