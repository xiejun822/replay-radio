(() => {
  const byId = id => document.getElementById(id);
  const box = document.createElement('section');
  box.className = 'visual-controls';
  box.innerHTML = `
    <div class="visual-controls-heading"><strong>识别歌曲</strong></div>
    <button class="button primary" type="button" id="identify-song" disabled>一键识别</button>
    <p class="field-note" role="status" id="identify-status">导入素材后，从播放位置取样 12 秒，联网匹配音乐指纹。</p>
    <div class="fields" id="identify-result" hidden>
      <strong id="identify-title"></strong>
      <button class="button ghost" type="button" id="identify-use">填入歌名</button>
      <label id="identify-lyrics-label">歌词预览<textarea id="identify-lyrics" rows="4" readonly></textarea></label>
      <button class="button ghost" type="button" id="identify-import">追加到歌词时间线</button>
    </div>`;
  byId('song-title').closest('section').querySelector('.editor-section-title').after(box);
  const button = byId('identify-song');
  const status = byId('identify-status');
  const result = byId('identify-result');
  const use = byId('identify-use');
  const append = byId('identify-import');
  const preview = byId('identify-lyrics');
  let file, player, pending, match;
  function clearResult() {
    result.style.display = 'none';
    match = null;
    preview.value = '';
  }
  clearResult();
  for (const [input, media] of [['media-input', 'audio-source'], ['video-input', 'video-source']]) {
    byId(input).addEventListener('change', event => {
      if (!event.target.files.length) return;
      pending?.abort();
      pending = null;
      file = event.target.files[0];
      player = byId(media);
      button.disabled = false;
      clearResult();
      status.textContent = '可拖到伴奏清楚的位置，再点识别。仅发送音乐指纹。';
    });
  }
  button.onclick = async () => {
    clearResult();
    const controller = new AbortController();
    pending = controller;
    button.disabled = true;
    const end = Number.isFinite(player.duration) ? player.duration : Infinity;
    const start = Math.max(0, Math.min(player.currentTime, end - 12));
    status.textContent = '正在取样、匹配歌曲并查询歌词…';
    try {
      const response = await fetch('/rr/recognize', {
        method: 'POST', headers: {'X-Sample-Start': String(start)}, body: file, signal: controller.signal
      });
      const data = await response.json();
      if (pending !== controller) return;
      if (!response.ok) throw new Error(data.error || '识别失败');
      if (!data.matched) {
        status.textContent = '未匹配到歌曲。可移到副歌或伴奏更清楚的位置再试。';
        return;
      }
      match = data;
      result.hidden = false;
      result.style.display = '';
      use.disabled = false;
      byId('identify-title').textContent = [data.title, data.artist].filter(Boolean).join(' · ');
      const lyrics = data.lyrics || {};
      const lines = (lyrics.lines || []).filter(line => line.text);
      preview.value = lines.map(line => {
        if (!lyrics.synced || !Number.isFinite(line.t)) return line.text;
        const cs = Math.round(line.t * 100);
        const minutes = String(Math.floor(cs / 6000)).padStart(2, '0');
        const seconds = (cs % 6000 / 100).toFixed(2).padStart(5, '0');
        return '[' + minutes + ':' + seconds + ']' + line.text;
      }).join('\n');
      byId('identify-lyrics-label').style.display = lines.length ? '' : 'none';
      append.style.display = lines.length ? '' : 'none';
      append.disabled = false;
      if (lines.length) {
        const timing = lyrics.synced ? '原版时间轴，翻唱或裁剪后可调整' : '纯文本，导入后可逐句打点';
        status.textContent = '歌词版本：' + [lyrics.title, lyrics.artist].filter(Boolean).join(' · ') + '（' + timing + '）';
      } else {
        status.textContent = lyrics.error ? '已识别歌曲，歌词服务暂不可用。' : '已识别歌曲，歌词库暂无对应歌词。';
      }
    } catch (error) {
      if (pending === controller) status.textContent = error.message;
    } finally {
      if (pending === controller) {
        pending = null;
        button.disabled = false;
      }
    }
  };
  use.onclick = () => {
    byId('song-title').value = match.title;
    byId('song-title').dispatchEvent(new Event('input'));
    use.disabled = true;
  };
  append.onclick = () => {
    const input = byId('lrc-text');
    const previous = input.value;
    input.value = preview.value;
    byId('lrc-import').click();
    input.value = previous;
    append.disabled = true;
  };
})();
