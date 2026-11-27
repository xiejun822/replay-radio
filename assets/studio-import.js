(async function () {
  const params = new URLSearchParams(location.search);
  const port = Number(params.get('radio'));
  const page = crypto.randomUUID().replaceAll('-', '');
  const alive = () => fetch('/rr/alive', {method:'POST', body:page}).catch(() => {});
  alive();
  setInterval(alive, 20000);
  window.addEventListener('pageshow', alive);
  window.addEventListener('pagehide', () => navigator.sendBeacon('/rr/bye', page));

  // Upstream loads the layout schema and saved draft before binding its controls.
  if (!document.getElementById('flow-background-preview-video')) {
    await new Promise(resolve => {
      const observer = new MutationObserver(() => {
        if (document.getElementById('flow-background-preview-video')) {
          observer.disconnect();
          resolve();
        }
      });
      observer.observe(document.body, {childList:true});
    });
  }
  const close = document.getElementById('shutdown-studio');
  if (Number.isInteger(port) && port > 0 && port <= 65535) {
    const back = document.createElement('a');
    back.className = close.className;
    back.href = 'http://127.0.0.1:' + port + '/';
    back.textContent = '返回 ReplayRadio';
    close.replaceWith(back);
    // 上游在导出期间还会更新这个控件的 disabled 状态。
    back.id = 'shutdown-studio';
  }
  const id = params.get('clip');
  if (!id) return;
  const status = document.getElementById('export-status');
  try {
    status.textContent = '正在载入 ReplayRadio 切片…';
    const base = '/rr/clip/';
    const info = await (await fetch(base + 'info/' + encodeURIComponent(id))).json();
    if (info.error) throw new Error(info.error);
    const response = await fetch(base + 'media/' + encodeURIComponent(id));
    if (!response.ok) throw new Error('无法读取切片，请确认 MP4 还在原位置');
    const saved = await window.SongcutPendantStorage.read().catch(() => null);
    const files = new DataTransfer();
    files.items.add(new File([await response.blob()], info.name, {type:'video/mp4'}));
    const input = document.getElementById('video-input');
    input.files = files.files;
    input.dispatchEvent(new Event('change'));
    if (saved) return;
    document.getElementById('visual-source-video').click();
    for (const [field, value] of [['song-title', info.song], ['song-artist', info.artist]]) {
      const control = document.getElementById(field);
      control.value = value;
      control.dispatchEvent(new Event('input'));
    }
  } catch (error) {
    status.textContent = error.message + '。也可以点「导入视频」选择本地文件。';
  }
})();
