// HaiAI Studio — Frontend State & Logic
const state = {
  currentTab: 'dashboard',
  authenticated: false,
  email: null,
  // Video Studio state
  videoRatio: '16:9',
  videoStyle: 'Điện ảnh 4K Hollywood',
  isContinuation: false,
  videoSessionId: null,
  sceneIndex: 1,
  storyboard: [], // [{ scene: 1, url: '/outputs/...', prompt: '...' }]
  videoImageFile: null,
  // Image Studio state
  aspectRatio: '1:1',
  imageFile: null,
  lastGeneratedImageUrl: null,
  // Chat state
  chatSessionId: null,
  // Gallery cache
  galleryItems: [],
  galleryFilter: 'all',
};

const TAB_TITLES = {
  dashboard: 'Tổng quan hệ thống (Dashboard)',
  video: 'HaiAI Video Studio — Sản xuất & Nối tiếp Cảnh quay',
  image: 'HaiAI Image Studio — Sáng tạo Hình ảnh 8K',
  chat: 'Trợ lý Đạo diễn & Biên kịch HaiAI',
  gallery: 'Thư viện Tác phẩm HaiAI',
};

document.addEventListener('DOMContentLoaded', () => {
  checkAuthStatus();
  setupDropzones();
  loadDashboardAndGallery();
});

// Navigation
function switchTab(tabId) {
  state.currentTab = tabId;
  document.querySelectorAll('.tab-view').forEach(el => el.classList.add('hidden'));

  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.classList.remove(
      'text-white',
      'bg-gradient-to-r',
      'from-indigo-600',
      'to-violet-600',
      'shadow-lg',
      'shadow-indigo-600/25',
      'font-semibold'
    );
    btn.classList.add('text-slate-400', 'hover:text-white', 'hover:bg-surface-800', 'font-medium');
  });

  const activeView = document.getElementById(`view-${tabId}`);
  const activeBtn = document.getElementById(`tab-btn-${tabId}`);
  if (activeView) activeView.classList.remove('hidden');
  if (activeBtn) {
    activeBtn.classList.remove('text-slate-400', 'hover:text-white', 'hover:bg-surface-800', 'font-medium');
    activeBtn.classList.add(
      'text-white',
      'bg-gradient-to-r',
      'from-indigo-600',
      'to-violet-600',
      'shadow-lg',
      'shadow-indigo-600/25',
      'font-semibold'
    );
  }

  const titleEl = document.getElementById('page-header-title');
  if (titleEl && TAB_TITLES[tabId]) {
    titleEl.innerText = TAB_TITLES[tabId];
  }

  if (tabId === 'dashboard' || tabId === 'gallery') {
    loadDashboardAndGallery();
  }
  lucide.createIcons();
}

function startSequentialFromDashboard() {
  switchTab('video');
  setVideoMode(true);
}

// Auth Status
async function checkAuthStatus() {
  try {
    const res = await fetch('/api/auth/status');
    const data = await res.json();
    const dot = document.getElementById('status-dot');
    const text = document.getElementById('status-text');
    const btnText = document.getElementById('auth-btn-text');

    state.authenticated = data.authenticated && data.is_alive;
    state.email = data.email;

    if (state.authenticated) {
      dot.className = 'w-2.5 h-2.5 rounded-full bg-emerald-400 shadow-sm shadow-emerald-400';
      text.innerText = data.email || 'Đã kết nối HaiAI Cloud';
      text.className = 'text-xs font-bold text-emerald-300 truncate';
      btnText.innerText = 'Đổi tài khoản';
    } else {
      dot.className = 'w-2.5 h-2.5 rounded-full bg-amber-400 animate-pulse';
      text.innerText = 'Chưa kết nối tài khoản';
      text.className = 'text-xs font-semibold text-amber-300 truncate';
      btnText.innerText = 'Đăng nhập OTP';
    }
  } catch (err) {
    console.error('Lỗi kiểm tra kết nối:', err);
  }
}

function openAuthModal() {
  document.getElementById('auth-modal').classList.remove('hidden');
  document.getElementById('otp-step-1').classList.remove('hidden');
  document.getElementById('otp-step-2').classList.add('hidden');
  hideModalMsg();
}

function closeAuthModal() {
  document.getElementById('auth-modal').classList.add('hidden');
}

function showModalMsg(text, isError = true) {
  const msg = document.getElementById('modal-msg');
  msg.innerText = text;
  msg.className = `p-3 rounded-xl text-xs font-medium ${
    isError ? 'bg-rose-500/10 border border-rose-500/20 text-rose-300' : 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-300'
  }`;
  msg.classList.remove('hidden');
}

function hideModalMsg() {
  document.getElementById('modal-msg').classList.add('hidden');
}

async function requestOtp() {
  const email = document.getElementById('auth-email-input').value.trim();
  if (!email || !email.includes('@')) {
    showModalMsg('Vui lòng nhập địa chỉ email hợp lệ.');
    return;
  }

  const btn = document.getElementById('btn-request-otp');
  const orig = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<span class="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></span><span>Đang gửi mã...</span>`;
  hideModalMsg();

  try {
    const res = await fetch('/api/auth/request-otp', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, region: 'US' }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Không thể gửi mã OTP.');

    document.getElementById('otp-step-1').classList.add('hidden');
    document.getElementById('otp-step-2').classList.remove('hidden');
    showModalMsg(`Đã gửi mã OTP tới ${email}. Vui lòng kiểm tra hộp thư!`, false);
  } catch (err) {
    showModalMsg(err.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = orig;
  }
}

function backToStep1() {
  document.getElementById('otp-step-1').classList.remove('hidden');
  document.getElementById('otp-step-2').classList.add('hidden');
  hideModalMsg();
}

async function verifyOtp() {
  const code = document.getElementById('auth-otp-input').value.trim();
  if (!code || code.length < 4) {
    showModalMsg('Vui lòng nhập mã OTP hợp lệ.');
    return;
  }

  const btn = document.getElementById('btn-verify-otp');
  const orig = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<span class="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></span><span>Đang xác thực...</span>`;
  hideModalMsg();

  try {
    const res = await fetch('/api/auth/verify-otp', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ otp_code: code }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Xác nhận OTP thất bại.');

    showModalMsg('Kết nối HaiAI thành công!', false);
    setTimeout(() => {
      closeAuthModal();
      checkAuthStatus();
    }, 1000);
  } catch (err) {
    showModalMsg(err.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = orig;
  }
}

// Video Mode & Options (Single vs Sequential Continuation)
function setVideoMode(isSeq) {
  state.isContinuation = isSeq;
  const btnSingle = document.getElementById('mode-btn-single');
  const btnSeq = document.getElementById('mode-btn-seq');
  const banner = document.getElementById('seq-mode-banner');
  const promptInput = document.getElementById('video-prompt');
  const btnGenText = document.getElementById('btn-generate-video-text');
  const label = document.getElementById('video-prompt-label');

  if (isSeq) {
    btnSeq.className = 'py-2.5 px-3 rounded-lg text-xs font-bold flex items-center justify-center space-x-1.5 transition bg-gradient-to-r from-cyan-600 to-indigo-600 text-white shadow';
    btnSingle.className = 'py-2.5 px-3 rounded-lg text-xs font-bold flex items-center justify-center space-x-1.5 transition text-slate-400 hover:text-white';
    banner.classList.remove('hidden');

    const nextSceneNum = state.storyboard.length + 1;
    state.sceneIndex = nextSceneNum;
    document.getElementById('seq-badge-title').innerText =
      nextSceneNum === 1
        ? 'Chuỗi Phim Nối Tiếp — Bắt đầu Cảnh #1 (Mở đầu)'
        : `Đang nối tiếp Cảnh #${nextSceneNum} (Từ Cảnh #${nextSceneNum - 1})`;

    label.innerHTML = `Diễn biến Cảnh #${nextSceneNum} <span class="text-rose-400">*</span>`;
    promptInput.placeholder =
      nextSceneNum === 1
        ? 'Nhập Cảnh mở đầu #1 (VD: Chàng hiệp sĩ đứng trước cổng thành cổ trong đêm mưa bão)...'
        : `Nhập diễn biến tiếp theo cho Cảnh #${nextSceneNum} (VD: Hiệp sĩ rút thanh kiếm phát sáng bước vào bên trong đại sảnh)...`;
    btnGenText.innerText = `Quay Cảnh Nối Tiếp #${nextSceneNum}`;
  } else {
    btnSingle.className = 'py-2.5 px-3 rounded-lg text-xs font-bold flex items-center justify-center space-x-1.5 transition bg-indigo-600 text-white shadow';
    btnSeq.className = 'py-2.5 px-3 rounded-lg text-xs font-bold flex items-center justify-center space-x-1.5 transition text-slate-400 hover:text-white';
    banner.classList.add('hidden');
    label.innerHTML = `Nội dung cảnh quay <span class="text-rose-400">*</span>`;
    promptInput.placeholder = 'Nhập ý tưởng của bạn (VD: Một phi hành gia bước đi trên hành tinh băng giá, nhìn thấy phế tích cổ đại phát sáng)...';
    btnGenText.innerText = 'Sản xuất Video Chuyên Nghiệp';
  }
}

function resetSequentialChain() {
  state.videoSessionId = null;
  state.storyboard = [];
  state.sceneIndex = 1;
  document.getElementById('storyboard-container').classList.add('hidden');
  setVideoMode(true);
}

function prepareNextScene() {
  setVideoMode(true);
  const promptEl = document.getElementById('video-prompt');
  promptEl.value = '';
  promptEl.focus();
}

function setVideoRatio(ratio) {
  state.videoRatio = ratio;
  ['16:9', '9:16', '1:1', '21:9', '4:3'].forEach(r => {
    const btn = document.getElementById(`vratio-${r.replace(':', '-')}`);
    if (!btn) return;
    if (r === ratio) {
      btn.className = 'vratio-btn border border-indigo-500 bg-indigo-500/20 text-white rounded-xl py-2.5 px-2 text-[11px] font-bold flex flex-col items-center space-y-1.5 transition';
    } else {
      btn.className = 'vratio-btn border border-surface-600 bg-surface-900 text-slate-400 hover:text-white rounded-xl py-2.5 px-2 text-[11px] font-bold flex flex-col items-center space-y-1.5 transition';
    }
  });
  const badge = document.getElementById('active-ratio-badge');
  if (badge) badge.innerText = `Khung hình: ${ratio}`;
}

function setVideoStyle(styleName) {
  state.videoStyle = styleName;
  const map = {
    'Điện ảnh 4K Hollywood': 'vstyle-hollywood',
    'Siêu thực tế Photorealistic': 'vstyle-real',
    'Hoạt hình 3D Pixar / Anime': 'vstyle-anime',
    'Cyberpunk Sci-Fi Neon': 'vstyle-cyber',
    'TVC Quảng cáo thương mại': 'vstyle-tvc',
  };
  Object.entries(map).forEach(([name, id]) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (name === styleName) {
      el.className = 'vstyle-btn text-xs px-3 py-1.5 rounded-lg border border-indigo-500 bg-indigo-500/20 text-white font-semibold transition';
    } else {
      el.className = 'vstyle-btn text-xs px-3 py-1.5 rounded-lg border border-surface-600 bg-surface-900 text-slate-300 hover:text-white transition';
    }
  });
}

function setAspectRatio(ratio) {
  state.aspectRatio = ratio;
  ['1:1', '16:9', '9:16', '21:9', '4:3'].forEach(r => {
    const btn = document.getElementById(`ratio-${r.replace(':', '-')}`);
    if (!btn) return;
    if (r === ratio) {
      btn.className = 'ratio-btn border border-purple-500 bg-purple-500/20 text-white rounded-xl py-2.5 px-2 text-[11px] font-bold flex flex-col items-center space-y-1.5 transition';
    } else {
      btn.className = 'ratio-btn border border-surface-600 bg-surface-900 text-slate-400 hover:text-white rounded-xl py-2.5 px-2 text-[11px] font-bold flex flex-col items-center space-y-1.5 transition';
    }
  });
}

// Upload Reference Image
function setupDropzones() {
  ['video', 'image'].forEach(type => {
    const dropzone = document.getElementById(`${type}-dropzone`);
    const input = document.getElementById(type === 'video' ? 'video-image-input' : 'image-file-input');
    if (!dropzone || !input) return;

    dropzone.addEventListener('click', (e) => {
      if (e.target.closest('button')) return;
      input.click();
    });

    dropzone.addEventListener('dragover', (e) => {
      e.preventDefault();
      dropzone.classList.add('border-indigo-500');
    });

    dropzone.addEventListener('dragleave', () => {
      dropzone.classList.remove('border-indigo-500');
    });

    dropzone.addEventListener('drop', (e) => {
      e.preventDefault();
      dropzone.classList.remove('border-indigo-500');
      if (e.dataTransfer.files && e.dataTransfer.files[0]) {
        uploadReferenceImage(e.dataTransfer.files[0], type);
      }
    });
  });
}

function handleImageUpload(event, type) {
  if (event.target.files && event.target.files[0]) {
    uploadReferenceImage(event.target.files[0], type);
  }
}

async function uploadReferenceImage(file, type) {
  const formData = new FormData();
  formData.append('file', file);

  try {
    const res = await fetch('/api/upload', {
      method: 'POST',
      body: formData,
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Upload thất bại.');

    if (type === 'video') {
      state.videoImageFile = data.local_path;
      document.getElementById('video-image-preview').src = data.url;
      document.getElementById('video-image-preview-container').classList.remove('hidden');
      document.getElementById('video-upload-prompt').classList.add('hidden');
    } else {
      state.imageFile = data.local_path;
      document.getElementById('image-file-preview').src = data.url;
      document.getElementById('image-file-preview-container').classList.remove('hidden');
      document.getElementById('image-upload-prompt').classList.add('hidden');
    }
  } catch (err) {
    alert('Lỗi tải ảnh lên: ' + err.message);
  }
}

function removeImage(type) {
  if (type === 'video') {
    state.videoImageFile = null;
    document.getElementById('video-image-input').value = '';
    document.getElementById('video-image-preview-container').classList.add('hidden');
    document.getElementById('video-upload-prompt').classList.remove('hidden');
  } else {
    state.imageFile = null;
    document.getElementById('image-file-input').value = '';
    document.getElementById('image-file-preview-container').classList.add('hidden');
    document.getElementById('image-upload-prompt').classList.remove('hidden');
  }
}

// VIDEO GENERATION
let videoTimerInterval = null;

async function generateVideo() {
  const prompt = document.getElementById('video-prompt').value.trim();
  if (!prompt) {
    alert('Vui lòng nhập mô tả cảnh quay.');
    return;
  }

  if (!state.authenticated) {
    openAuthModal();
    return;
  }

  const btn = document.getElementById('btn-generate-video');
  btn.disabled = true;

  document.getElementById('video-idle-state').classList.add('hidden');
  document.getElementById('video-result-container').classList.add('hidden');
  document.getElementById('video-loading-state').classList.remove('hidden');
  document.getElementById('video-timer').classList.remove('hidden');

  let seconds = 0;
  const timerEl = document.getElementById('timer-seconds');
  timerEl.innerText = '0';
  if (videoTimerInterval) clearInterval(videoTimerInterval);
  videoTimerInterval = setInterval(() => {
    seconds++;
    timerEl.innerText = seconds;
    if (seconds === 15) {
      document.getElementById('video-progress-text').innerText = 'HaiAI Director đang thiết lập góc máy & đồng nhất nhân vật...';
    } else if (seconds === 50) {
      document.getElementById('video-progress-text').innerText = 'Đang kết xuất chuyển động 4K và tổng hợp âm thanh vòm...';
    } else if (seconds === 110) {
      document.getElementById('video-progress-text').innerText = 'Đang hoàn thiện hậu kỳ và đóng gói MP4...';
    }
  }, 1000);

  try {
    const currentSceneNumber = state.isContinuation ? state.storyboard.length + 1 : 1;

    const payload = {
      prompt: prompt,
      image_paths: state.videoImageFile ? [state.videoImageFile] : [],
      aspect_ratio: state.videoRatio,
      is_continuation: state.isContinuation && state.videoSessionId !== null,
      session_id: state.videoSessionId,
      scene_index: currentSceneNumber,
      style_preset: state.videoStyle,
      timeout: 600.0,
    };

    const res = await fetch('/api/video/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Không thể tạo video.');

    if (data.session_id) {
      state.videoSessionId = data.session_id;
    }

    if (data.videos && data.videos.length > 0) {
      const videoUrl = data.videos[0];
      const player = document.getElementById('video-player');
      player.src = videoUrl;
      document.getElementById('video-download-btn').href = videoUrl;

      // Update storyboard chain
      if (!state.isContinuation) {
        state.storyboard = [{ scene: 1, url: videoUrl, prompt: prompt }];
      } else {
        state.storyboard.push({
          scene: state.storyboard.length + 1,
          url: videoUrl,
          prompt: prompt,
        });
      }
      renderStoryboard();
      if (state.isContinuation) {
        setVideoMode(true);
      }

      document.getElementById('video-loading-state').classList.add('hidden');
      document.getElementById('video-result-container').classList.remove('hidden');
      player.play().catch(() => {});
      loadDashboardAndGallery();
    } else {
      throw new Error('Không nhận được video trả về.');
    }
  } catch (err) {
    alert('Lỗi tạo video: ' + err.message);
    document.getElementById('video-loading-state').classList.add('hidden');
    document.getElementById('video-idle-state').classList.remove('hidden');
  } finally {
    clearInterval(videoTimerInterval);
    btn.disabled = false;
  }
}

function renderStoryboard() {
  const container = document.getElementById('storyboard-container');
  const list = document.getElementById('storyboard-list');
  const countEl = document.getElementById('storyboard-count');

  const statScenes = document.getElementById('stat-scenes');
  if (statScenes) statScenes.innerText = `${state.storyboard.length} cảnh`;

  if (state.storyboard.length === 0) {
    container.classList.add('hidden');
    return;
  }

  container.classList.remove('hidden');
  countEl.innerText = `${state.storyboard.length} cảnh liên kết`;
  list.innerHTML = '';

  state.storyboard.forEach((item) => {
    const card = document.createElement('button');
    card.type = 'button';
    card.onclick = () => {
      const player = document.getElementById('video-player');
      player.src = item.url;
      document.getElementById('video-download-btn').href = item.url;
      player.play().catch(() => {});
    };
    card.className = 'shrink-0 w-44 p-2.5 rounded-xl bg-surface-900 hover:bg-surface-800 border border-cyan-500/30 text-left space-y-1.5 transition group';
    card.innerHTML = `
      <div class="flex items-center justify-between">
        <span class="text-[10px] font-extrabold uppercase px-2 py-0.5 rounded bg-cyan-500/20 text-cyan-300">Cảnh #${item.scene}</span>
        <i data-lucide="play-circle" class="w-3.5 h-3.5 text-slate-400 group-hover:text-cyan-400"></i>
      </div>
      <p class="text-[11px] text-slate-300 truncate">${escapeHtml(item.prompt)}</p>
    `;
    list.appendChild(card);
  });
  lucide.createIcons();
}

// IMAGE GENERATION
async function generateImage() {
  const prompt = document.getElementById('image-prompt').value.trim();
  if (!prompt) {
    alert('Vui lòng nhập mô tả bức ảnh.');
    return;
  }

  if (!state.authenticated) {
    openAuthModal();
    return;
  }

  const btn = document.getElementById('btn-generate-image');
  btn.disabled = true;

  document.getElementById('image-idle-state').classList.add('hidden');
  document.getElementById('image-result-container').classList.add('hidden');
  document.getElementById('image-loading-state').classList.remove('hidden');

  try {
    const payload = {
      prompt: prompt,
      image_paths: state.imageFile ? [state.imageFile] : [],
      aspect_ratio: state.aspectRatio,
    };

    const res = await fetch('/api/image/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Không thể tạo ảnh.');

    if (data.images && data.images.length > 0) {
      const imgUrl = data.images[0];
      state.lastGeneratedImageUrl = imgUrl;
      document.getElementById('image-result').src = imgUrl;
      document.getElementById('image-download-btn').href = imgUrl;

      document.getElementById('image-loading-state').classList.add('hidden');
      document.getElementById('image-result-container').classList.remove('hidden');
      loadDashboardAndGallery();
    } else {
      throw new Error('Không nhận được ảnh trả về.');
    }
  } catch (err) {
    alert('Lỗi tạo ảnh: ' + err.message);
    document.getElementById('image-loading-state').classList.add('hidden');
    document.getElementById('image-idle-state').classList.remove('hidden');
  } finally {
    btn.disabled = false;
  }
}

function useImageForVideo() {
  const prompt = document.getElementById('image-prompt').value.trim();
  switchTab('video');
  if (prompt) {
    document.getElementById('video-prompt').value = prompt;
  }
}

// CHAT ASSISTANT
async function sendChatMessage(e) {
  e.preventDefault();
  const input = document.getElementById('chat-input');
  const text = input.value.trim();
  if (!text) return;

  if (!state.authenticated) {
    openAuthModal();
    return;
  }

  const container = document.getElementById('chat-messages');

  const userBubble = document.createElement('div');
  userBubble.className = 'flex items-start justify-end space-x-3';
  userBubble.innerHTML = `
    <div class="bg-gradient-to-r from-indigo-600 to-violet-600 rounded-2xl rounded-tr-none p-3.5 max-w-xl text-sm text-white shadow">
      ${escapeHtml(text)}
    </div>
    <div class="w-8 h-8 rounded-xl bg-surface-700 text-slate-300 flex items-center justify-center shrink-0 border border-white/10">
      <i data-lucide="user" class="w-4 h-4"></i>
    </div>
  `;
  container.appendChild(userBubble);
  input.value = '';
  container.scrollTop = container.scrollHeight;
  lucide.createIcons();

  const typingBubble = document.createElement('div');
  typingBubble.className = 'flex items-start space-x-3';
  typingBubble.innerHTML = `
    <div class="w-8 h-8 rounded-xl bg-indigo-600/20 text-indigo-400 flex items-center justify-center shrink-0 border border-indigo-500/30">
      <i data-lucide="sparkles" class="w-4 h-4"></i>
    </div>
    <div class="bg-surface-800 border border-white/[0.06] rounded-2xl rounded-tl-none p-3.5 max-w-xl text-sm text-slate-400 flex items-center space-x-2">
      <span class="w-2 h-2 rounded-full bg-indigo-400 animate-ping"></span>
      <span>HaiAI đang biên kịch...</span>
    </div>
  `;
  container.appendChild(typingBubble);
  container.scrollTop = container.scrollHeight;
  lucide.createIcons();

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        prompt: text,
        session_id: state.chatSessionId,
      }),
    });
    const data = await res.json();
    typingBubble.remove();

    if (!res.ok) throw new Error(data.detail || 'Lỗi kết nối.');
    if (data.session_id) state.chatSessionId = data.session_id;

    const botBubble = document.createElement('div');
    botBubble.className = 'flex items-start space-x-3';
    botBubble.innerHTML = `
      <div class="w-8 h-8 rounded-xl bg-indigo-600/20 text-indigo-400 flex items-center justify-center shrink-0 border border-indigo-500/30">
        <i data-lucide="sparkles" class="w-4 h-4"></i>
      </div>
      <div class="bg-surface-800 border border-white/[0.06] rounded-2xl rounded-tl-none p-4 max-w-2xl text-sm text-slate-200 space-y-3">
        <p class="whitespace-pre-wrap leading-relaxed">${escapeHtml(data.reply)}</p>
        <div class="flex flex-wrap gap-2 pt-1">
          <button onclick="useAsVideoPrompt(${JSON.stringify(data.reply)})" class="text-[11px] text-indigo-300 hover:text-white font-semibold flex items-center space-x-1 border border-indigo-500/30 bg-indigo-500/15 px-2.5 py-1 rounded-lg transition">
            <i data-lucide="clapperboard" class="w-3 h-3"></i>
            <span>Đưa sang Tạo Video</span>
          </button>
          <button onclick="useAsImagePrompt(${JSON.stringify(data.reply)})" class="text-[11px] text-purple-300 hover:text-white font-semibold flex items-center space-x-1 border border-purple-500/30 bg-purple-500/15 px-2.5 py-1 rounded-lg transition">
            <i data-lucide="palette" class="w-3 h-3"></i>
            <span>Đưa sang Tạo Ảnh</span>
          </button>
        </div>
      </div>
    `;
    container.appendChild(botBubble);
    container.scrollTop = container.scrollHeight;
    lucide.createIcons();
  } catch (err) {
    typingBubble.remove();
    const errorBubble = document.createElement('div');
    errorBubble.className = 'text-xs text-rose-400 p-2 text-center';
    errorBubble.innerText = 'Lỗi: ' + err.message;
    container.appendChild(errorBubble);
  }
}

function clearChat() {
  state.chatSessionId = null;
  document.getElementById('chat-messages').innerHTML = `
    <div class="flex items-start space-x-3">
      <div class="w-8 h-8 rounded-xl bg-indigo-600/20 text-indigo-400 flex items-center justify-center shrink-0 border border-indigo-500/30">
        <i data-lucide="sparkles" class="w-4 h-4"></i>
      </div>
      <div class="bg-surface-800 border border-white/[0.06] rounded-2xl rounded-tl-none p-4 max-w-2xl text-sm text-slate-200 leading-relaxed">
        Cuộc trò chuyện đã được làm mới. Hãy nhập ý tưởng kịch bản của bạn!
      </div>
    </div>
  `;
  lucide.createIcons();
}

function useAsVideoPrompt(text) {
  switchTab('video');
  document.getElementById('video-prompt').value = text;
}

function useAsImagePrompt(text) {
  switchTab('image');
  document.getElementById('image-prompt').value = text;
}

// DASHBOARD & GALLERY
async function loadDashboardAndGallery() {
  try {
    const res = await fetch('/api/gallery');
    const data = await res.json();
    state.galleryItems = data.items || [];

    // Update Stats
    if (data.stats) {
      document.getElementById('stat-videos').innerText = data.stats.videos;
      document.getElementById('stat-images').innerText = data.stats.images;
      document.getElementById('stat-storage').innerText = `${data.stats.storage_mb} MB`;
      document.getElementById('sidebar-gallery-count').innerText = data.stats.total;
    }

    renderDashboardRecent();
    renderGalleryGrid();
  } catch (err) {
    console.error('Lỗi tải dữ liệu:', err);
  }
}

function loadGallery() {
  loadDashboardAndGallery();
}

function filterGallery(filterType) {
  state.galleryFilter = filterType;
  ['all', 'video', 'image'].forEach(f => {
    const btn = document.getElementById(`gfilter-${f}`);
    if (!btn) return;
    if (f === filterType) {
      btn.className = 'gfilter-btn px-3 py-1.5 rounded-lg text-xs font-semibold bg-indigo-600 text-white';
    } else {
      btn.className = 'gfilter-btn px-3 py-1.5 rounded-lg text-xs font-semibold bg-surface-800 text-slate-300 hover:text-white';
    }
  });
  renderGalleryGrid();
}

function renderDashboardRecent() {
  const grid = document.getElementById('dashboard-recent-grid');
  if (!grid) return;

  const recent = state.galleryItems.slice(0, 4);
  if (recent.length === 0) {
    grid.innerHTML = `
      <div class="col-span-full py-10 text-center text-slate-500 text-xs">
        Chưa có sản phẩm nào. Hãy bấm <b>Mở Studio Tạo Video</b> để khởi tạo thước phim đầu tiên!
      </div>
    `;
    return;
  }

  grid.innerHTML = '';
  recent.forEach(item => {
    grid.appendChild(createMediaCard(item));
  });
  lucide.createIcons();
}

function renderGalleryGrid() {
  const grid = document.getElementById('gallery-grid');
  if (!grid) return;

  const items = state.galleryItems.filter(it =>
    state.galleryFilter === 'all' ? true : it.type === state.galleryFilter
  );

  if (items.length === 0) {
    grid.innerHTML = `
      <div class="col-span-full py-16 text-center text-slate-500 space-y-2">
        <i data-lucide="film" class="w-10 h-10 mx-auto text-slate-600"></i>
        <p class="text-sm">Chưa có tác phẩm nào trong danh mục này.</p>
      </div>
    `;
    lucide.createIcons();
    return;
  }

  grid.innerHTML = '';
  items.forEach(item => {
    grid.appendChild(createMediaCard(item));
  });
  lucide.createIcons();
}

function createMediaCard(item) {
  const card = document.createElement('div');
  card.className = 'glass-card rounded-2xl overflow-hidden shadow-lg group hover:border-indigo-500/40 transition flex flex-col justify-between';

  const mediaHtml = item.type === 'video'
    ? `<div class="aspect-video bg-black flex items-center justify-center relative">
         <video src="${item.url}" class="w-full h-full object-cover" controls playsinline></video>
       </div>`
    : `<div class="aspect-video bg-black/50 flex items-center justify-center overflow-hidden">
         <img src="${item.url}" class="w-full h-full object-cover group-hover:scale-105 transition duration-300">
       </div>`;

  card.innerHTML = `
    ${mediaHtml}
    <div class="p-3.5 space-y-2">
      <div class="flex items-center justify-between text-xs">
        <span class="font-semibold text-slate-200 truncate max-w-[150px]">${item.name}</span>
        <span class="text-slate-400 text-[10px]">${item.size_mb} MB</span>
      </div>
      <div class="flex items-center justify-between pt-1">
        <span class="text-[10px] uppercase font-bold tracking-wider px-2 py-0.5 rounded-md ${
          item.type === 'video' ? 'bg-indigo-500/20 text-indigo-300' : 'bg-purple-500/20 text-purple-300'
        }">
          ${item.type === 'video' ? 'HaiAI Video' : 'HaiAI Image'}
        </span>
        <a href="${item.url}" download class="px-2.5 py-1 rounded-lg bg-surface-700 hover:bg-surface-600 text-slate-200 text-xs font-medium flex items-center space-x-1 transition">
          <i data-lucide="download" class="w-3 h-3"></i>
          <span>Tải về</span>
        </a>
      </div>
    </div>
  `;
  return card;
}

function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
