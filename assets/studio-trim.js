(() => {
  const byId = id => document.getElementById(id);
  const panel = document.createElement('section');
  panel.className = 'visual-controls';
  panel.setAttribute('aria-label', '素材裁剪');
  panel.innerHTML = `
    <div class="visual-controls-heading"><strong>素材裁剪</strong><span id="trim-length"></span></div>
    <div class="fields two">
      <label>起点（分:秒）<input id="trim-start" value="00:00"></label>
      <label>终点（分:秒）<input id="trim-end" placeholder="03:00"></label>
    </div>
    <div class="visual-source-switch">
      <button type="button" id="trim-mark-start">当前位置设为起点</button>
      <button type="button" id="trim-mark-end">当前位置设为终点</button>
    </div>
    <div class="layout-reset-actions">
      <button type="button" class="button primary" id="trim-apply">应用裁剪</button>
      <a class="button ghost" id="trim-save" hidden>保存片段</a>
    </div>
    <p class="field-note" role="status" id="trim-status">先导入素材，再选择要保留的范围。</p>`;
  byId('media-input').closest('.upload-grid').after(panel);
  const startField = byId('trim-start');
  const endField = byId('trim-end');
  const apply = byId('trim-apply');
  const save = byId('trim-save');
  const status = byId('trim-status');
  let activeInput, player, source, request;

  function clock(seconds) {
    const ms = Math.round(seconds * 1000);
    const minutes = String(Math.floor(ms / 60000)).padStart(2, '0');
    const secs = String(Math.floor(ms / 1000) % 60).padStart(2, '0');
    const fraction = String(ms % 1000).padStart(3, '0').replace(/0+$/, '');
    return minutes + ':' + secs + (fraction ? '.' + fraction : '');
  }

  function seconds(text) {
    const value = text.trim();
    if (/^\d+(\.\d{1,3})?$/.test(value)) return Number(value);
    const parts = /^(\d+):([0-5]?\d(?:\.\d{1,3})?)$/.exec(value);
    return parts ? Number(parts[1]) * 60 + Number(parts[2]) : NaN;
  }

  function update() {
    const ready = !!source && Number.isFinite(player.duration) && player.readyState >= 1;
    const start = seconds(startField.value);
    const end = seconds(endField.value);
    const valid = ready && start >= 0 && end > start && end <= player.duration + 0.001;
    byId('trim-length').textContent = valid ? '保留 ' + clock(end - start) : '';
    for (const field of [startField, endField, byId('trim-mark-start'), byId('trim-mark-end')]) {
      field.disabled = !ready || !!request;
    }
    apply.disabled = !valid || !!request;
    if (ready && !request && !valid) status.textContent = '请输入有效时间，终点须晚于起点且在素材时长内。';
  }

  for (const [inputId, playerId] of [['media-input', 'audio-source'], ['video-input', 'video-source']]) {
    const input = byId(inputId);
    const media = byId(playerId);
    input.addEventListener('change', () => {
      if (!input.files[0]) return;
      request?.abort();
      request = null;
      activeInput = input;
      source = input.files[0];
      player = media;
      if (save.hasAttribute('href')) URL.revokeObjectURL(save.href);
      save.removeAttribute('href');
      save.style.display = 'none';
      status.textContent = '建议在歌词打点前裁剪，片段从 00:00 开始。';
      startField.value = '00:00';
      endField.value = '';
      update();
    });
    media.addEventListener('loadedmetadata', () => {
      if (media !== player || !Number.isFinite(media.duration)) return;
      endField.value = clock(media.duration);
      update();
    });
  }
  for (const [field, button] of [[startField, 'trim-mark-start'], [endField, 'trim-mark-end']]) {
    const edit = () => {
      status.textContent = '点击「应用裁剪」后，将用所选片段继续制作。';
      update();
    };
    field.addEventListener('input', edit);
    byId(button).onclick = () => {
      field.value = clock(player.currentTime);
      edit();
    };
  }

  apply.onclick = async () => {
    const start = seconds(startField.value);
    const end = seconds(endField.value);
    const video = activeInput.id === 'video-input';
    const controller = new AbortController();
    request = controller;
    player.pause();
    update();
    status.textContent = '正在裁剪 ' + clock(end - start) + ' 的片段…';
    try {
      const response = await fetch('/rr/trim', {
        method: 'POST', body: source, signal: controller.signal,
        headers: {'X-Media-Kind': video ? 'video' : 'audio', 'X-Trim-Start': start, 'X-Trim-End': end}
      });
      if (!response.ok) throw new Error((await response.json()).error || '裁剪失败');
      const blob = await response.blob();
      if (request !== controller) return;
      const range = [start, end].map(t => clock(t).replace(':', '-')).join('_');
      const name = source.name.replace(/\.[^.]+$/, '') + '_' + range + (video ? '.mp4' : '.mp3');
      const files = new DataTransfer();
      files.items.add(new File([blob], name, {type: blob.type}));
      request = null;
      activeInput.files = files.files;
      activeInput.dispatchEvent(new Event('change'));
      save.href = URL.createObjectURL(blob);
      save.download = name;
      save.hidden = false;
      save.style.display = '';
      status.textContent = '已应用片段 ' + clock(start) + ' – ' + clock(end) + '。可继续制作或保存片段。';
    } catch (error) {
      if (request === controller) status.textContent = error.message;
    } finally {
      if (request === controller) request = null;
      update();
    }
  };
  save.style.display = 'none';
  update();
})();
