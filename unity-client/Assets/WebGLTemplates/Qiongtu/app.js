"use strict";

// 仅承载 Unity 实例的加载、退出和全屏，三维内容及业务操作继续由 Unity 提供。
(() => {
  const canvas = document.getElementById("unity-canvas");
  const loading = document.getElementById("loading");
  const status = document.getElementById("status");
  const detail = document.getElementById("detail");
  const progress = document.getElementById("progress");
  const restart = document.getElementById("restart");
  const fullscreen = document.getElementById("fullscreen");
  let instance = null;
  let exiting = false;

  function showError(message) {
    loading.hidden = false;
    status.textContent = "沙盘未能成功载入";
    detail.textContent = String(message);
    progress.hidden = true;
    restart.hidden = false;
    fullscreen.hidden = true;
  }

  restart.addEventListener("click", () => window.location.reload());
  canvas.addEventListener("contextmenu", event => event.preventDefault());
  canvas.addEventListener("pointerdown", () => canvas.focus());
  canvas.addEventListener("mousedown", event => {
    // 禁止中键自动滚动等浏览器默认手势，实际相机输入仍交给 Unity 处理。
    if (event.button === 1 || event.button === 2) event.preventDefault();
  });
  canvas.addEventListener("wheel", event => event.preventDefault(), { passive: false });
  canvas.addEventListener("dragstart", event => event.preventDefault());
  fullscreen.addEventListener("click", () => {
    if (instance) instance.SetFullscreen(1);
  });

  window.addEventListener("qiongtu-exit", async () => {
    if (!instance || exiting) return;
    exiting = true;
    fullscreen.hidden = true;
    loading.hidden = false;
    status.textContent = "正在退出沙盘…";
    progress.hidden = true;
    try {
      await instance.Quit();
      instance = null;
      canvas.hidden = true;
      status.textContent = "已退出当前沙盘";
      detail.textContent = "已保存的浏览器存档会保留；重新进入后，可切换到主持人在存档面板恢复。";
      restart.hidden = false;
    } catch (error) {
      showError("退出未完成，请刷新页面重新进入。" + String(error));
    }
  });

  if (window.location.protocol === "file:") {
    showError("请使用项目中的“打开网页版.cmd”启动，通过本地网页地址访问。");
    return;
  }
  if (!window.WebAssembly) {
    showError("当前浏览器不支持运行所需功能，请使用近期版本的桌面 Edge 或 Chrome。");
    return;
  }
  const config = { ...window.qiongtuBuild, devicePixelRatio: Math.min(window.devicePixelRatio || 1, 2) };
  const loaderUrl = config.loaderUrl;
  delete config.loaderUrl;
  config.showBanner = (message, type) => {
    if (type === "error") showError(message);
    else console.warn(message);
  };
  const loader = document.createElement("script");
  loader.src = loaderUrl;
  loader.onerror = () => showError("无法读取沙盘程序文件，请确认服务目录包含完整的 Web 构建产物。");
  loader.onload = () => {
    createUnityInstance(canvas, config, value => {
      progress.value = value;
      status.textContent = "正在载入三维地图… " + Math.round(value * 100) + "%";
    }).then(player => {
      instance = player;
      loading.hidden = true;
      fullscreen.hidden = false;
      canvas.focus();
    }).catch(showError);
  };
  document.body.appendChild(loader);
})();
