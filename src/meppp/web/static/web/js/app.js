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

  const recoveryCode = document.querySelector("[data-recovery-code]");
  const recoveryCopy = document.querySelector("[data-copy-recovery-code]");
  const recoveryStatus = document.querySelector("[data-copy-recovery-status]");
  recoveryCopy?.addEventListener("click", async () => {
    if (!recoveryCode || !recoveryStatus) return;
    try {
      await navigator.clipboard.writeText(recoveryCode.value);
      recoveryStatus.textContent = "恢复码已复制。请保存到密码管理器或其他安全位置。";
    } catch (_error) {
      recoveryCode.focus();
      recoveryCode.select();
      recoveryStatus.textContent = "浏览器未允许自动复制，已选中恢复码，请手动复制。";
    }
  });

  const sourceComposer = document.querySelector("[data-source-composer]");
  const sourceInput = sourceComposer?.querySelector("[data-source-url]");
  const sourceStatus = sourceComposer?.querySelector("[data-source-status]");
  const xSourceEnabled = Boolean(sourceComposer?.querySelector(".source-platform-x"));
  const youtubeSourceEnabled = Boolean(
    sourceComposer?.querySelector(".source-platform-youtube"),
  );
  const xStatusPath = /^\/(?:i\/(?:web\/)?status|[A-Za-z0-9_]{1,30}\/status)\/[0-9]{1,20}(?:\/(?:photo|video)\/[1-4])?\/?$/;
  const youtubeVideoId = /^[A-Za-z0-9_-]{11}$/;
  const youtubeVideoPath = /^\/(?:shorts|live|embed)\/([A-Za-z0-9_-]{11})\/?$/;

  const sourceProvider = (value) => {
    if (!value.trim()) return "";
    try {
      const url = new URL(value.trim());
      if (
        url.protocol !== "https:" ||
        url.username ||
        url.password ||
        (url.port && url.port !== "443") ||
        url.hash
      ) {
        return "unsupported";
      }
      const host = url.hostname.toLowerCase();
      if (["x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"].includes(host)) {
        return xStatusPath.test(url.pathname) ? "x" : "unsupported";
      }
      if (["youtu.be", "www.youtu.be"].includes(host)) {
        const parts = url.pathname.split("/").filter(Boolean);
        return parts.length === 1 && youtubeVideoId.test(parts[0])
          ? "youtube"
          : "unsupported";
      }
      if (["youtube.com", "www.youtube.com", "m.youtube.com"].includes(host)) {
        let externalId = "";
        if (url.pathname === "/watch" || url.pathname === "/watch/") {
          const candidates = url.searchParams.getAll("v");
          externalId = candidates.length === 1 ? candidates[0] : "";
        } else {
          externalId = url.pathname.match(youtubeVideoPath)?.[1] || "";
        }
        return youtubeVideoId.test(externalId) ? "youtube" : "unsupported";
      }
    } catch (_error) {
      return "unsupported";
    }
    return "unsupported";
  };

  const renderSource = () => {
    if (!sourceInput || !sourceStatus) return;
    const provider = sourceProvider(sourceInput.value);
    sourceStatus.classList.remove("is-ready", "has-error");
    if (!provider) {
      sourceStatus.textContent = "粘贴公开链接后会自动识别平台。";
      return;
    }
    if (provider === "x" && xSourceEnabled) {
      sourceStatus.textContent = "已识别为 X Post；发布后会保留原始署名和链接。";
      sourceStatus.classList.add("is-ready");
      return;
    }
    if (provider === "youtube" && youtubeSourceEnabled) {
      sourceStatus.textContent = "已识别为 YouTube 视频；发布后会显示官方来源卡片。";
      sourceStatus.classList.add("is-ready");
      return;
    }
    if (provider === "x" || provider === "youtube") {
      sourceStatus.textContent = `管理员当前没有开放 ${provider === "x" ? "X" : "YouTube"} 来源分享。`;
    } else {
      sourceStatus.textContent = "链接尚未识别；请粘贴完整的 X Post 或 YouTube 视频 HTTPS 链接。";
    }
    sourceStatus.classList.add("has-error");
  };

  const clearSource = () => {
    if (!sourceInput) return;
    sourceInput.value = "";
    renderSource();
  };

  sourceInput?.addEventListener("input", renderSource);
  renderSource();

  const videoComposer = document.querySelector("[data-video-composer]");
  const videoInput = videoComposer?.querySelector("[data-video-input]");
  const videoStatus = videoComposer?.querySelector("[data-video-status]");
  const videoPreview = videoComposer?.querySelector("[data-video-preview]");
  const videoPlayer = videoComposer?.querySelector("[data-video-preview-player]");
  const videoRemove = videoComposer?.querySelector("[data-video-remove]");
  let videoObjectUrl = "";

  const releaseVideoUrl = () => {
    if (videoObjectUrl) URL.revokeObjectURL(videoObjectUrl);
    videoObjectUrl = "";
  };

  const renderVideo = () => {
    if (!videoInput || !videoStatus || !videoPreview || !videoPlayer) return;
    releaseVideoUrl();
    const file = videoInput.files[0];
    if (!file) {
      videoPlayer.removeAttribute("src");
      videoPlayer.load();
      videoPreview.hidden = true;
      videoStatus.textContent = "还没有选择视频";
      videoStatus.classList.remove("has-error");
      return;
    }
    const allowedTypes = new Set(["video/mp4", "video/webm"]);
    const maximumBytes = Number(videoInput.dataset.maxBytes || "0");
    const issues = [];
    if (!allowedTypes.has(file.type)) issues.push("格式不支持");
    if (file.size > maximumBytes) issues.push("超过 20 MB");
    videoObjectUrl = URL.createObjectURL(file);
    videoPlayer.src = videoObjectUrl;
    videoPreview.hidden = false;
    videoStatus.textContent = issues.length
      ? `${file.name}；${issues.join("；")}`
      : `${file.name} · ${(file.size / 1024 / 1024).toFixed(1)} MB`;
    videoStatus.classList.toggle("has-error", issues.length > 0);
  };

  videoInput?.addEventListener("change", () => {
    if (videoInput.files.length) {
      const selectedImages = document.querySelector("[data-image-input]");
      if (selectedImages?.files.length) {
        const replaceImages = window.confirm("选择视频会移除已经选好的图片，是否继续？");
        if (!replaceImages) {
          videoInput.value = "";
          renderVideo();
          return;
        }
        selectedImages.value = "";
        selectedImages.dispatchEvent(new Event("change"));
      }
      if (sourceInput?.value) {
        const replaceSource = window.confirm("选择视频会移除已经粘贴的来源链接，是否继续？");
        if (!replaceSource) {
          videoInput.value = "";
          renderVideo();
          return;
        }
        clearSource();
      }
    }
    renderVideo();
  });
  videoRemove?.addEventListener("click", () => {
    if (!videoInput) return;
    videoInput.value = "";
    renderVideo();
  });
  window.addEventListener("pagehide", releaseVideoUrl, { once: true });
  renderVideo();

  const composer = document.querySelector("[data-image-composer]");
  const imageInput = composer?.querySelector("[data-image-input]");
  const altState = document.querySelector("#id_image_alt_texts");
  const previewList = composer?.querySelector("[data-image-preview-list]");
  const status = composer?.querySelector("[data-image-status]");

  const maximumImages = Number(imageInput?.dataset.maxImages || "0");
  const maximumBytes = Number(imageInput?.dataset.maxBytes || "0");
  const allowedTypes = new Set(["image/jpeg", "image/png", "image/webp"]);
  let altTexts = [];
  let objectUrls = [];

  const releaseObjectUrls = () => {
    objectUrls.forEach((url) => URL.revokeObjectURL(url));
    objectUrls = [];
  };

  const syncAltState = () => {
    if (!altState) return;
    altState.value = JSON.stringify(altTexts);
  };

  const removeImage = (removedIndex) => {
    if (!imageInput) return;
    const transfer = new DataTransfer();
    Array.from(imageInput.files).forEach((file, index) => {
      if (index !== removedIndex) transfer.items.add(file);
    });
    imageInput.files = transfer.files;
    altTexts.splice(removedIndex, 1);
    renderImages();
  };

  const renderImages = () => {
    if (!imageInput || !previewList || !status) return;
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

  imageInput?.addEventListener("change", () => {
    if (imageInput.files.length && videoInput?.files.length) {
      const replaceVideo = window.confirm("选择图片会移除已经选好的视频，是否继续？");
      if (!replaceVideo) {
        imageInput.value = "";
        altTexts = [];
        renderImages();
        return;
      }
      videoInput.value = "";
      renderVideo();
    }
    if (imageInput.files.length && sourceInput?.value) {
      const replaceSource = window.confirm("选择图片会移除已经粘贴的来源链接，是否继续？");
      if (!replaceSource) {
        imageInput.value = "";
        altTexts = [];
        renderImages();
        return;
      }
      clearSource();
    }
    altTexts = Array.from(imageInput.files, () => "");
    renderImages();
  });
  window.addEventListener("pagehide", releaseObjectUrls, { once: true });
  renderImages();

  document.querySelectorAll("[data-image-picker], [data-video-picker]").forEach((picker) => {
    picker.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      picker.click();
    });
  });

  const publishingForm = document.querySelector("[data-publishing-form]");
  const publishSubmit = publishingForm?.querySelector("[data-publish-submit]");
  const composerPanels = document.querySelectorAll("[data-composer-panel]");
  const composerShortcuts = Array.from(
    document.querySelectorAll("[data-composer-shortcut]"),
  );
  const shortcutsByMode = new Map(
    composerShortcuts.map((shortcut) => [shortcut.dataset.composerShortcut, shortcut]),
  );

  const activateComposerShortcut = (
    shortcut,
    { confirmSwitch = false, focus = false } = {},
  ) => {
    const mode = shortcut?.dataset.composerShortcut;
    if (!mode) return false;
    const targetPanel = mode === "x" || mode === "youtube" ? "source" : mode;
    if (confirmSwitch) {
      const hasImages = Boolean(imageInput?.files.length);
      const hasVideo = Boolean(videoInput?.files.length);
      const hasSource = Boolean(sourceInput?.value);
      const switchingToImage = mode === "image" && (hasVideo || hasSource);
      const switchingToVideo = mode === "video" && (hasImages || hasSource);
      const switchingToSource = targetPanel === "source" && (hasImages || hasVideo);
      if (switchingToImage || switchingToVideo || switchingToSource) {
        const labels = {
          image: "图片",
          video: "视频",
          source: "来源卡片",
        };
        const confirmed = window.confirm(
          `切换到${labels[targetPanel]}会移除当前已选的其他媒体，是否继续？`,
        );
        if (!confirmed) return false;
        if (mode !== "image" && imageInput) {
          imageInput.value = "";
          altTexts = [];
          renderImages();
        }
        if (mode !== "video" && videoInput) {
          videoInput.value = "";
          renderVideo();
        }
        if (targetPanel !== "source") clearSource();
      }
    }
    if (mode === "x" && sourceInput) {
      sourceInput.placeholder = "粘贴 X Post 的公开链接";
      if (!sourceInput.value && sourceStatus) {
        sourceStatus.textContent = "请粘贴一条公开的 X Post HTTPS 链接。";
      }
    }
    if (mode === "youtube" && sourceInput) {
      sourceInput.placeholder = "粘贴 YouTube 视频的公开链接";
      if (!sourceInput.value && sourceStatus) {
        sourceStatus.textContent = "请粘贴一个公开的 YouTube 视频 HTTPS 链接。";
      }
    }
    if (sourceInput?.value && targetPanel === "source") renderSource();
    composerPanels.forEach((panel) => {
      panel.classList.toggle("is-active", panel.dataset.composerPanel === targetPanel);
    });
    composerShortcuts.forEach((item) => {
      if (item === shortcut) item.setAttribute("aria-current", "true");
      else item.removeAttribute("aria-current");
    });
    if (focus) {
      const focusTarget = {
        text: document.querySelector("#id_body"),
        image: document.querySelector("[data-image-picker]"),
        video: document.querySelector("[data-video-picker]"),
        x: sourceInput,
        youtube: sourceInput,
        topics: document.querySelector("#composer-topics input"),
      }[mode];
      window.setTimeout(() => focusTarget?.focus(), 0);
    }
    return true;
  };

  composerShortcuts.forEach((shortcut) => {
    shortcut.addEventListener("click", (event) => {
      event.preventDefault();
      activateComposerShortcut(shortcut, { confirmSwitch: true, focus: true });
    });
  });

  let initialComposeMode = new URLSearchParams(window.location.search).get("compose");
  if (!shortcutsByMode.has(initialComposeMode)) {
    const errorPanel = document.querySelector("[data-composer-panel].has-error");
    if (errorPanel) {
      initialComposeMode =
        errorPanel.dataset.composerPanel === "source"
          ? sourceProvider(sourceInput?.value || "") === "youtube" || !xSourceEnabled
            ? "youtube"
            : "x"
          : errorPanel.dataset.composerPanel;
    } else if (sourceInput?.value) {
      initialComposeMode = sourceProvider(sourceInput.value) === "youtube" ? "youtube" : "x";
    } else {
      initialComposeMode = "text";
    }
  }
  if (!shortcutsByMode.has(initialComposeMode)) {
    initialComposeMode = shortcutsByMode.has("text")
      ? "text"
      : composerShortcuts[0]?.dataset.composerShortcut;
  }
  publishingForm?.classList.add("is-enhanced");
  if (publishingForm && initialComposeMode) {
    if (window.location.hash === "#home-composer") {
      document
        .querySelectorAll(".mobile-menu[open]")
        .forEach((menu) => menu.removeAttribute("open"));
    }
    activateComposerShortcut(shortcutsByMode.get(initialComposeMode), {
      focus: window.location.hash === "#home-composer",
    });
  }
  window.addEventListener("hashchange", () => {
    if (window.location.hash === "#home-composer") {
      document
        .querySelectorAll(".mobile-menu[open]")
        .forEach((menu) => menu.removeAttribute("open"));
      activateComposerShortcut(shortcutsByMode.get("text"), { focus: true });
    }
  });
  publishingForm?.addEventListener("submit", () => {
    if (sourceInput?.value && (imageInput?.files.length || videoInput?.files.length)) {
      const keepSource = window.confirm(
        "来源卡片不能与本地媒体同时发布。选择“确定”保留来源链接；选择“取消”保留本地媒体。",
      );
      if (keepSource) {
        if (imageInput) {
          imageInput.value = "";
          altTexts = [];
          renderImages();
        }
        if (videoInput) {
          videoInput.value = "";
          renderVideo();
        }
      } else {
        clearSource();
      }
    }
    if (publishSubmit) {
      publishSubmit.disabled = true;
      publishSubmit.textContent = videoInput?.files.length ? "正在处理视频…" : "正在发布…";
    }
    publishingForm.setAttribute("aria-busy", "true");
  });
})();
