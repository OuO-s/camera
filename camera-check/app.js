const channels = {
  A: makeChannel('A'),
  B: makeChannel('B'),
};

const els = {
  start: document.querySelector('#startButton'),
  refresh: document.querySelector('#refreshButton'),
  swap: document.querySelector('#swapButton'),
  count: document.querySelector('#deviceCount'),
  alert: document.querySelector('#alertPanel'),
  alertTitle: document.querySelector('#alertTitle'),
  alertText: document.querySelector('#alertText'),
  system: document.querySelector('#systemState'),
  systemText: document.querySelector('#systemStateText'),
  sdk: document.querySelector('#sdkState'),
  clock: document.querySelector('#clock'),
  canvas: document.querySelector('#snapshotCanvas'),
};

let cameras = [];

function makeChannel(id) {
  return {
    id,
    image: document.querySelector(`#image${id}`),
    select: document.querySelector(`#select${id}`),
    state: document.querySelector(`#state${id}`),
    empty: document.querySelector(`#empty${id}`),
    live: document.querySelector(`#live${id}`),
    shot: document.querySelector(`#shot${id}`),
    resolution: document.querySelector(`#resolution${id}`),
    fps: document.querySelector(`#fps${id}`),
    serial: null,
  };
}

function setSystem(state, text) {
  els.system.dataset.state = state;
  els.systemText.textContent = text;
}

function showAlert(title, text) {
  els.alertTitle.textContent = title;
  els.alertText.textContent = text;
  els.alert.hidden = false;
}

function hideAlert() {
  els.alert.hidden = true;
}

function setChannelState(channel, state, text) {
  channel.state.className = `feed-state ${state || ''}`;
  channel.state.textContent = text;
}

function cameraBySerial(serial) {
  return cameras.find((camera) => camera.serial === serial);
}

function stopChannel(channel) {
  channel.image.removeAttribute('src');
  channel.image.hidden = true;
  channel.empty.hidden = false;
  channel.live.hidden = true;
  channel.shot.disabled = true;
  channel.serial = null;
  channel.resolution.textContent = '— × —';
  channel.fps.textContent = '— FPS';
  setChannelState(channel, '', '未启动');
}

async function fetchCameras() {
  const response = await fetch('/api/cameras', { cache: 'no-store' });
  if (!response.ok) throw new Error(`SDK 服务返回 ${response.status}`);
  const data = await response.json();
  cameras = data.cameras;
  els.sdk.textContent = `v${data.sdk_version}`;
  els.count.textContent = String(data.online_count).padStart(2, '0');
  return data;
}

function populateSelectors() {
  const previous = { A: channels.A.select.value, B: channels.B.select.value };
  Object.values(channels).forEach((channel, index) => {
    channel.select.replaceChildren();
    cameras.forEach((camera) => {
      const connection = camera.online ? camera.usb || '在线' : '离线';
      channel.select.add(new Option(
        `${camera.role} · SN ${camera.serial} · ${connection}`,
        camera.serial,
      ));
    });
    const preserved = cameras.some((camera) => camera.serial === previous[channel.id]);
    channel.select.value = preserved ? previous[channel.id] : (cameras[index]?.serial || '');
    channel.select.disabled = !cameras.length;
  });
  els.refresh.disabled = false;
  els.swap.disabled = cameras.length < 2;
}

async function refreshDevices({ announce = false } = {}) {
  try {
    const before = cameras.filter((camera) => camera.online).map((camera) => camera.serial).join(',');
    const data = await fetchCameras();
    populateSelectors();
    const after = cameras.filter((camera) => camera.online).map((camera) => camera.serial).join(',');
    if (announce) {
      if (data.online_count === 2) {
        showAlert('两台 D435 均已识别', 'SDK 已检测到左右两台相机，可以启动双路画面。');
      } else {
        showOfflineDiagnostic();
      }
    } else if (before !== after && before) {
      showAlert('USB 设备状态变化', `当前在线 ${data.online_count}/2 台，请重新启动双摄。`);
    }
    return data;
  } catch (error) {
    els.sdk.textContent = '服务离线';
    els.count.textContent = '00';
    setSystem('error', 'SDK 服务离线');
    showAlert('RealSense 服务不可用', `请用 realsense_server.py 启动本页。${error.message}`);
    throw error;
  }
}

function showOfflineDiagnostic() {
  const offline = cameras.filter((camera) => !camera.online);
  if (!offline.length) return;
  const names = offline.map((camera) => `${camera.role}（SN ${camera.serial}）`).join('、');
  showAlert('SDK 未检测到全部相机', `${names}离线。请连接 USB 3.0 端口，确认数据线支持 USB 3，并重新插拔后点击“刷新设备”。`);
}

async function waitForStreaming(serial, timeoutMs = 8000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 350));
    const data = await fetchCameras();
    const camera = data.cameras.find((item) => item.serial === serial);
    if (camera?.streaming) return camera;
    if (camera?.last_error) throw new Error(camera.last_error);
  }
  throw new Error('等待相机首帧超时');
}

async function openChannel(channel, serial) {
  stopChannel(channel);
  const camera = cameraBySerial(serial);
  if (!camera?.online) {
    setChannelState(channel, 'error', '设备离线');
    return false;
  }

  channel.serial = serial;
  channel.image.onerror = () => {
    channel.empty.hidden = false;
    channel.image.hidden = true;
    channel.live.hidden = true;
    channel.shot.disabled = true;
    setChannelState(channel, 'error', '视频流中断');
  };
  channel.image.hidden = false;
  channel.image.src = `/stream/${encodeURIComponent(serial)}?t=${Date.now()}`;
  setChannelState(channel, '', '等待首帧…');

  try {
    const streamState = await waitForStreaming(serial);
    channel.empty.hidden = true;
    channel.live.hidden = false;
    channel.shot.disabled = false;
    channel.resolution.textContent = `${streamState.width || 640} × ${streamState.height || 480}`;
    channel.fps.textContent = `${streamState.fps || 15} FPS`;
    setChannelState(channel, 'ok', `在线 · ${camera.role}`);
    return true;
  } catch (error) {
    stopChannel(channel);
    setChannelState(channel, 'error', '取流失败');
    showAlert(`${camera.role}启动失败`, error.message);
    return false;
  }
}

async function startSelectedChannels() {
  hideAlert();
  const aSerial = channels.A.select.value;
  const bSerial = channels.B.select.value;
  if (aSerial === bSerial) {
    showAlert('设备选择重复', '相机 A 与相机 B 必须选择不同的 D435 序列号。');
    return;
  }

  const [aOk, bOk] = await Promise.all([
    openChannel(channels.A, aSerial),
    openChannel(channels.B, bSerial),
  ]);
  const active = Number(aOk) + Number(bOk);
  setSystem(
    active ? 'live' : 'error',
    active === 2 ? '双路画面在线' : active === 1 ? '单路画面在线' : '没有可用画面',
  );
  if (active < 2) showOfflineDiagnostic();
}

async function start() {
  els.start.disabled = true;
  els.start.querySelector('span:last-child').textContent = '正在连接 RealSense…';
  setSystem('idle', '正在检测');
  hideAlert();
  try {
    const data = await refreshDevices();
    if (!data.online_count) {
      setSystem('error', '未检测到 D435');
      showOfflineDiagnostic();
      return;
    }
    await startSelectedChannels();
  } catch {
    // refreshDevices 已呈现错误。
  } finally {
    els.start.disabled = false;
    els.start.querySelector('span:last-child').textContent = '重新启动双摄';
  }
}

function takeSnapshot(channel) {
  if (!channel.serial || channel.image.hidden) return;
  const width = channel.image.naturalWidth;
  const height = channel.image.naturalHeight;
  if (!width || !height) return;
  els.canvas.width = width;
  els.canvas.height = height;
  els.canvas.getContext('2d').drawImage(channel.image, 0, 0, width, height);
  els.canvas.toBlob((blob) => {
    if (!blob) return;
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    const stamp = new Date().toISOString().replaceAll(':', '-').replace('T', '_').slice(0, 19);
    link.href = url;
    link.download = `D435-${channel.serial}-${stamp}.png`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }, 'image/png');
}

els.start.addEventListener('click', start);
els.refresh.addEventListener('click', () => refreshDevices({ announce: true }));
els.swap.addEventListener('click', async () => {
  const a = channels.A.select.value;
  channels.A.select.value = channels.B.select.value;
  channels.B.select.value = a;
  await startSelectedChannels();
});

Object.values(channels).forEach((channel) => {
  channel.select.addEventListener('change', () => openChannel(channel, channel.select.value));
  channel.shot.addEventListener('click', () => takeSnapshot(channel));
});

window.addEventListener('beforeunload', () => Object.values(channels).forEach(stopChannel));

setInterval(() => {
  els.clock.textContent = new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(new Date());
}, 250);

(async function initialize() {
  try {
    const data = await refreshDevices();
    setSystem(
      data.online_count ? 'idle' : 'error',
      data.online_count ? `发现 ${data.online_count} 台 D435` : '未检测到 D435',
    );
    if (data.online_count < 2) showOfflineDiagnostic();
    setInterval(() => refreshDevices().catch(() => {}), 2500);
  } catch {
    // refreshDevices 已呈现完整错误信息。
  }
})();
