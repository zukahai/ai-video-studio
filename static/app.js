// HaiAI Studio — Frontend State & Logic
const state = {
  currentTab: 'dashboard',
  authenticated: false,
  email: null,
  // Video Studio state
  videoRatio: '16:9',
  videoDuration: 5,
  videoStyle: 'Điện ảnh 4K Hollywood',
  isContinuation: false,
  videoSessionId: null,
  sceneIndex: 1,
  storyboard: [], // [{ scene: 1, url: '/outputs/...', prompt: '...' }]
  videoImages: [], // Multi-image array: [{ id, local_path, url, name }]
  get videoImageFile() {
    return this.videoImages.length > 0 ? this.videoImages[0].local_path : null;
  },
  // Image Studio state
  aspectRatio: '1:1',
  imageFiles: [],  // Multi-image array: [{ id, local_path, url, name }]
  get imageFile() {
    return this.imageFiles.length > 0 ? this.imageFiles[0].local_path : null;
  },
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
  restoreFromUrlParams();
  checkAuthStatus();
  setupDropzones();
  loadDashboardAndGallery();
});

window.addEventListener('popstate', () => {
  restoreFromUrlParams();
});

// Update URL parameters without reloading
function updateUrlParams() {
  try {
    const url = new URL(window.location.href);
    url.searchParams.set('tab', state.currentTab);

    if (state.currentTab === 'video') {
      url.searchParams.set('mode', state.isContinuation ? 'seq' : 'single');
      if (state.videoRatio) url.searchParams.set('ratio', state.videoRatio);
      if (state.videoDuration) url.searchParams.set('duration', state.videoDuration);
      if (state.videoStyle) url.searchParams.set('style', state.videoStyle);
    } else if (state.currentTab === 'image') {
      if (state.aspectRatio) url.searchParams.set('ratio', state.aspectRatio);
      url.searchParams.delete('mode');
      url.searchParams.delete('style');
      url.searchParams.delete('duration');
    } else {
      url.searchParams.delete('mode');
      url.searchParams.delete('ratio');
      url.searchParams.delete('style');
      url.searchParams.delete('duration');
    }

    window.history.replaceState({}, '', url.pathname + url.search);
    localStorage.setItem('haiai_last_tab', state.currentTab);
  } catch (err) {
    console.warn('Lỗi lưu URL params:', err);
  }
}

// Restore active tab and options from URL or localStorage
function restoreFromUrlParams() {
  try {
    const params = new URLSearchParams(window.location.search);
    const hash = (window.location.hash || '').replace('#', '').trim();
    
    // 1. Tab parameter
    let targetTab = params.get('tab') || hash;
    if (!targetTab || !TAB_TITLES[targetTab]) {
      const savedTab = localStorage.getItem('haiai_last_tab');
      if (savedTab && TAB_TITLES[savedTab]) {
        targetTab = savedTab;
      }
    }
    if (!targetTab || !TAB_TITLES[targetTab]) {
      targetTab = 'dashboard';
    }

    // 2. Mode parameter
    const mode = params.get('mode');
    if (mode === 'seq' || mode === 'sequential') {
      setVideoMode(true, false);
    } else if (mode === 'single') {
      setVideoMode(false, false);
    }

    // 3. Ratio parameter
    const ratio = params.get('ratio') || params.get('aspect');
    if (ratio && ['16:9', '9:16', '1:1', '21:9', '4:3'].includes(ratio)) {
      if (targetTab === 'image') {
        setAspectRatio(ratio, false);
      } else {
        setVideoRatio(ratio, false);
      }
    }

    // 4. Style parameter
    const style = params.get('style');
    if (style) {
      setVideoStyle(style, false);
    }

    // 5. Duration parameter
    const duration = parseInt(params.get('duration') || params.get('sec') || localStorage.getItem('haiai_duration') || '5', 10);
    if (!isNaN(duration) && duration > 0) {
      setVideoDuration(duration, false);
    } else {
      setVideoDuration(5, false);
    }

    // Switch to target tab
    switchTab(targetTab, false);
  } catch (err) {
    console.warn('Lỗi đọc URL params:', err);
    switchTab('dashboard', false);
  }
}

// Navigation
function switchTab(tabId, syncUrl = true) {
  state.currentTab = tabId;
  document.querySelectorAll('.tab-view').forEach(el => el.classList.add('hidden'));

  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.classList.remove(
      'text-white',
      'bg-gradient-to-r',
      'from-sky-500',
      'to-blue-600',
      'shadow-lg',
      'shadow-sky-600/25',
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
      'from-sky-500',
      'to-blue-600',
      'shadow-lg',
      'shadow-sky-600/25',
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

  if (syncUrl) {
    updateUrlParams();
  }
}

function startSequentialFromDashboard() {
  switchTab('video');
  setVideoMode(true);
}

// Auth Status
async function checkAuthStatus(force = false) {
  try {
    const url = force ? '/api/auth/status?force=true' : '/api/auth/status';
    const res = await fetch(url);
    const data = await res.json();
    const dot = document.getElementById('status-dot');
    const text = document.getElementById('status-text');
    const btnText = document.getElementById('auth-btn-text');

    state.authenticated = data.authenticated && data.is_alive;
    state.email = data.email;
    state.quota = data.quota || {};

    // Update tokens on Header & Sidebar & Dashboard
    const tokens = (data.quota && data.quota.context_window_tokens) ? data.quota.context_window_tokens.toLocaleString() : '350,000';
    const headerTokens = document.getElementById('header-quota-tokens');
    if (headerTokens) headerTokens.innerText = tokens;

    const statTokens = document.getElementById('stat-tokens');
    if (statTokens) statTokens.innerText = tokens;

    const viewerIdEl = document.getElementById('sidebar-viewer-id');
    if (viewerIdEl && data.viewer_id) {
      viewerIdEl.innerText = data.viewer_id;
      viewerIdEl.title = `Viewer ID: ${data.viewer_id}`;
    }

    // Update Usage Component (Exact live data: Gói miễn phí & Token bổ sung)
    if (data.usage) {
      const free = data.usage.free_plan || {};
      const add = data.usage.additional_tokens || {};

      // Dashboard Usage Card
      const uFreeName = document.getElementById('usage-free-name');
      if (uFreeName && free.name) uFreeName.innerText = free.name;

      const uFreePercent = document.getElementById('usage-free-percent');
      if (uFreePercent) uFreePercent.innerText = free.label || `Đã dùng ${free.used_percent || 0}%`;

      const uFreeReset = document.getElementById('usage-free-reset');
      if (uFreeReset) uFreeReset.innerText = free.subtitle || 'Giới hạn hàng tuần sẽ đặt lại sớm';

      const uFreeBar = document.getElementById('usage-free-bar');
      if (uFreeBar) uFreeBar.style.width = `${free.used_percent || 0}%`;

      const formatCapitalCon = (str) => {
        if (!str) return str;
        return str.replace(/\(còn/g, '(Còn').replace(/\bcòn\b/g, 'Còn');
      };

      const uAddName = document.getElementById('usage-add-name');
      if (uAddName && add.name) uAddName.innerText = add.name;

      const uAddTokens = document.getElementById('usage-add-tokens');
      if (uAddTokens) uAddTokens.innerText = formatCapitalCon(add.label) || 'Đã dùng 0% (Còn 1 tỷ token)';

      const uAddSub = document.getElementById('usage-add-subtitle');
      if (uAddSub) uAddSub.innerText = add.subtitle || 'Không bao giờ hết hạn';

      const uAddBar = document.getElementById('usage-add-bar');
      if (uAddBar) uAddBar.style.width = `${add.used_percent || 0}%`;

      const uCtaLabel = document.getElementById('usage-cta-label');
      if (uCtaLabel && data.usage.cta_label) uCtaLabel.innerText = data.usage.cta_label;

      // Sidebar Mini Usage
      const sFreeName = document.getElementById('sidebar-free-name');
      if (sFreeName && free.name) sFreeName.innerText = free.name;

      const sFreePercent = document.getElementById('sidebar-free-percent');
      if (sFreePercent) sFreePercent.innerText = free.label || `Đã dùng ${free.used_percent || 0}%`;

      const sFreeReset = document.getElementById('sidebar-free-reset');
      if (sFreeReset) sFreeReset.innerText = free.subtitle || 'Giới hạn hàng tuần';

      const sFreeBar = document.getElementById('sidebar-free-bar');
      if (sFreeBar) sFreeBar.style.width = `${free.used_percent || 0}%`;

      const sAddName = document.getElementById('sidebar-add-name');
      if (sAddName && add.name) sAddName.innerText = add.name;

      const sAddPercent = document.getElementById('sidebar-add-percent');
      if (sAddPercent) sAddPercent.innerText = `Đã dùng ${add.used_percent || 0}%`;

      let formattedLeft = '(Còn 1 tỷ token)';
      if (add.tokens_left) {
        let clean = formatCapitalCon(add.tokens_left);
        if (clean.includes('(')) {
          formattedLeft = `(${clean.split('(')[1]}`;
        } else {
          formattedLeft = clean.startsWith('Còn') ? `(${clean})` : clean;
        }
      }
      const sTokensLeft = document.getElementById('sidebar-tokens-left');
      if (sTokensLeft) sTokensLeft.innerText = formattedLeft;

      const sAddBar = document.getElementById('sidebar-add-bar');
      if (sAddBar) sAddBar.style.width = `${add.used_percent || 0}%`;
    }

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
function setVideoMode(isSeq, syncUrl = true) {
  state.isContinuation = isSeq;
  const btnSingle = document.getElementById('mode-btn-single');
  const btnSeq = document.getElementById('mode-btn-seq');
  const banner = document.getElementById('seq-mode-banner');
  const promptInput = document.getElementById('video-prompt');
  const btnGenText = document.getElementById('btn-generate-video-text');
  const label = document.getElementById('video-prompt-label');

  if (isSeq) {
    if (btnSeq) btnSeq.className = 'py-2.5 px-3 rounded-lg text-xs font-bold flex items-center justify-center space-x-1.5 transition bg-gradient-to-r from-cyan-600 to-indigo-600 text-white shadow';
    if (btnSingle) btnSingle.className = 'py-2.5 px-3 rounded-lg text-xs font-bold flex items-center justify-center space-x-1.5 transition text-slate-400 hover:text-white';
    if (banner) banner.classList.remove('hidden');

    const nextSceneNum = state.storyboard.length + 1;
    state.sceneIndex = nextSceneNum;
    const badge = document.getElementById('seq-badge-title');
    if (badge) {
      badge.innerText =
        nextSceneNum === 1
          ? 'Chuỗi Phim Nối Tiếp — Bắt đầu Cảnh #1 (Mở đầu)'
          : `Đang nối tiếp Cảnh #${nextSceneNum} (Từ Cảnh #${nextSceneNum - 1})`;
    }

    if (label) label.innerHTML = `Diễn biến Cảnh #${nextSceneNum} <span class="text-rose-400">*</span>`;
    if (promptInput) {
      promptInput.placeholder =
        nextSceneNum === 1
          ? 'Nhập Cảnh mở đầu #1 (VD: Chàng hiệp sĩ đứng trước cổng thành cổ trong đêm mưa bão)...'
          : `Nhập diễn biến tiếp theo cho Cảnh #${nextSceneNum} (VD: Hiệp sĩ rút thanh kiếm phát sáng bước vào bên trong đại sảnh)...`;
    }
    if (btnGenText) btnGenText.innerText = `Quay Cảnh Nối Tiếp #${nextSceneNum}`;
  } else {
    if (btnSingle) btnSingle.className = 'py-2.5 px-3 rounded-lg text-xs font-bold flex items-center justify-center space-x-1.5 transition bg-sky-600 text-white shadow shadow-sky-600/30';
    if (btnSeq) btnSeq.className = 'py-2.5 px-3 rounded-lg text-xs font-bold flex items-center justify-center space-x-1.5 transition text-slate-400 hover:text-white';
    if (banner) banner.classList.add('hidden');
    if (label) label.innerHTML = `Nội dung cảnh quay <span class="text-rose-400">*</span>`;
    if (promptInput) promptInput.placeholder = 'Nhập ý tưởng của bạn (VD: Một phi hành gia bước đi trên hành tinh băng giá, nhìn thấy phế tích cổ đại phát sáng)...';
    if (btnGenText) btnGenText.innerText = 'Sản xuất Video Chuyên Nghiệp';
  }

  if (syncUrl) {
    updateUrlParams();
  }
}

function resetSequentialChain() {
  state.videoSessionId = null;
  state.storyboard = [];
  state.sceneIndex = 1;
  const sContainer = document.getElementById('storyboard-container');
  if (sContainer) sContainer.classList.add('hidden');
  setVideoMode(true);
}

function prepareNextScene() {
  setVideoMode(true);
  const promptEl = document.getElementById('video-prompt');
  if (promptEl) {
    promptEl.value = '';
    promptEl.focus();
  }
}

function setVideoRatio(ratio, syncUrl = true) {
  state.videoRatio = ratio;
  ['16:9', '9:16', '1:1', '21:9', '4:3'].forEach(r => {
    const btn = document.getElementById(`vratio-${r.replace(':', '-')}`);
    if (!btn) return;
    if (r === ratio) {
      btn.className = 'vratio-btn border border-sky-400 bg-sky-500/20 text-white rounded-xl py-2.5 px-2 text-[11px] font-bold flex flex-col items-center space-y-1.5 transition shadow-sm shadow-sky-500/30';
    } else {
      btn.className = 'vratio-btn border border-surface-600 bg-surface-900 text-slate-400 hover:text-white rounded-xl py-2.5 px-2 text-[11px] font-bold flex flex-col items-center space-y-1.5 transition';
    }
  });
  const badge = document.getElementById('active-ratio-badge');
  if (badge) badge.innerText = `Khung hình: ${ratio}`;

  if (syncUrl) {
    updateUrlParams();
  }
}

function setVideoDuration(seconds, syncUrl = true) {
  seconds = parseInt(seconds, 10);
  if (isNaN(seconds) || seconds < 5) seconds = 5;
  if (seconds > 300) seconds = 300;
  state.videoDuration = seconds;

  // Update display badges
  const display = document.getElementById('video-duration-display');
  if (display) display.innerText = `${seconds} giây`;

  const monitorBadge = document.getElementById('active-duration-badge');
  if (monitorBadge) monitorBadge.innerText = `Thời lượng: ${seconds}s`;

  // Update preset buttons (5s, 8s, 10s, 15s, 20s)
  const presets = [5, 8, 10, 15, 20];
  const isPreset = presets.includes(seconds);

  presets.forEach(sec => {
    const btn = document.getElementById(`vduration-${sec}`);
    if (!btn) return;
    if (sec === seconds) {
      btn.className = 'vduration-btn py-2 px-1 rounded-xl border border-sky-400 bg-sky-500/20 text-white text-xs font-bold transition flex items-center justify-center shadow-sm shadow-sky-500/30';
    } else {
      btn.className = 'vduration-btn py-2 px-1 rounded-xl border border-surface-600 bg-surface-900 text-slate-400 hover:text-white text-xs font-bold transition flex items-center justify-center';
    }
  });

  // Custom button & input
  const customBtn = document.getElementById('vduration-custom-btn');
  const customInput = document.getElementById('vduration-custom-input');
  if (customBtn) {
    if (!isPreset) {
      customBtn.className = 'vduration-btn py-2 px-1 rounded-xl border border-sky-400 bg-sky-500/20 text-white text-[11px] font-bold transition flex items-center justify-center shadow-sm shadow-sky-500/30';
    } else {
      customBtn.className = 'vduration-btn py-2 px-1 rounded-xl border border-surface-600 bg-surface-900 text-slate-400 hover:text-white text-[11px] font-bold transition flex items-center justify-center';
    }
  }

  if (customInput && !isPreset) {
    customInput.value = seconds;
  }

  localStorage.setItem('haiai_duration', seconds);

  if (syncUrl) {
    updateUrlParams();
  }
}

function handleCustomDuration(val) {
  let num = parseInt(val, 10);
  if (!isNaN(num)) {
    if (num > 300) num = 300;
    setVideoDuration(num, true);
  }
}

function focusCustomDuration() {
  const input = document.getElementById('vduration-custom-input');
  if (input) {
    input.focus();
    if (!input.value) {
      input.value = state.videoDuration || 5;
    }
    setVideoDuration(input.value, true);
  }
}

function setVideoStyle(styleName, syncUrl = true) {
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
      el.className = 'vstyle-btn text-xs px-3 py-1.5 rounded-lg border border-sky-400 bg-sky-500/20 text-white font-semibold transition shadow-sm shadow-sky-500/30';
    } else {
      el.className = 'vstyle-btn text-xs px-3 py-1.5 rounded-lg border border-surface-600 bg-surface-900 text-slate-300 hover:text-white transition';
    }
  });

  if (syncUrl) {
    updateUrlParams();
  }
}

function setAspectRatio(ratio, syncUrl = true) {
  state.aspectRatio = ratio;
  ['1:1', '16:9', '9:16', '21:9', '4:3'].forEach(r => {
    const btn = document.getElementById(`ratio-${r.replace(':', '-')}`);
    if (!btn) return;
    if (r === ratio) {
      btn.className = 'ratio-btn border border-sky-400 bg-sky-500/20 text-white rounded-xl py-2.5 px-2 text-[11px] font-bold flex flex-col items-center space-y-1.5 transition shadow-sm shadow-sky-500/30';
    } else {
      btn.className = 'ratio-btn border border-surface-600 bg-surface-900 text-slate-400 hover:text-white rounded-xl py-2.5 px-2 text-[11px] font-bold flex flex-col items-center space-y-1.5 transition';
    }
  });

  if (syncUrl) {
    updateUrlParams();
  }
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
      if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
        uploadReferenceImages(e.dataTransfer.files, type);
      }
    });
  });
}

function handleImageUpload(event, type) {
  if (event.target.files && event.target.files.length > 0) {
    uploadReferenceImages(event.target.files, type);
  }
}

// Upload One or Multiple Reference Images
async function uploadReferenceImages(files, type) {
  const fileArray = Array.from(files).filter(f => f.type && f.type.startsWith('image/'));
  if (fileArray.length === 0) return;

  const uploadPromises = fileArray.map(async (file) => {
    const formData = new FormData();
    formData.append('file', file);
    const res = await fetch('/api/upload', {
      method: 'POST',
      body: formData,
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Upload thất bại.');
    return {
      id: 'img_' + Math.random().toString(36).substring(2, 9),
      local_path: data.local_path,
      url: data.url,
      name: file.name || 'image.png',
    };
  });

  try {
    const uploaded = await Promise.all(uploadPromises);
    if (type === 'video') {
      state.videoImages.push(...uploaded);
    } else {
      state.imageFiles.push(...uploaded);
    }
    renderImageThumbnails(type);

    const totalCount = (type === 'video' ? state.videoImages : state.imageFiles).length;
    showToast(`Đã thêm ${uploaded.length} ảnh! (Hiện có ${totalCount} ảnh tham chiếu)`);
  } catch (err) {
    alert('Lỗi tải ảnh lên: ' + err.message);
  }
}

// Render Multi-image Thumbnails Grid
function renderImageThumbnails(type) {
  const isVideo = type === 'video';
  const list = isVideo ? state.videoImages : state.imageFiles;
  const container = document.getElementById(isVideo ? 'video-image-preview-container' : 'image-file-preview-container');
  const badge = document.getElementById(isVideo ? 'video-images-count-badge' : 'image-images-count-badge');
  const countText = document.getElementById(isVideo ? 'video-images-count-text' : 'image-images-count-text');
  const prompt = document.getElementById(isVideo ? 'video-upload-prompt' : 'image-upload-prompt');
  const input = document.getElementById(isVideo ? 'video-image-input' : 'image-file-input');

  if (!container) return;

  if (list.length === 0) {
    container.classList.add('hidden');
    container.innerHTML = '';
    if (badge) badge.classList.add('hidden');
    if (prompt) prompt.classList.remove('hidden');
    if (input) input.value = '';
    return;
  }

  container.classList.remove('hidden');
  if (badge) {
    badge.classList.remove('hidden');
    if (countText) countText.innerText = `${list.length} ảnh`;
  }

  // Thumbnails Grid
  container.innerHTML = list.map((item, index) => `
    <div class="relative group rounded-xl overflow-hidden border border-white/20 bg-surface-900 shadow-md">
      <img src="${item.url}" alt="Ảnh tham chiếu #${index + 1}" class="w-20 h-20 object-cover rounded-xl transition duration-200 group-hover:scale-105">
      <div class="absolute inset-0 bg-black/50 opacity-0 group-hover:opacity-100 transition flex items-center justify-center">
        <button type="button" onclick="removeSingleImage('${type}', '${item.id}', event)" class="bg-rose-600 hover:bg-rose-500 text-white rounded-full p-1.5 shadow-lg transition" title="Xóa ảnh này">
          <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
        </button>
      </div>
      <span class="absolute bottom-1 left-1 px-1.5 py-0.5 rounded bg-black/70 text-[9px] font-mono text-white/90">#${index + 1}</span>
    </div>
  `).join('') + `
    <!-- Plus box to add more images -->
    <button type="button" onclick="document.getElementById('${isVideo ? 'video-image-input' : 'image-file-input'}').click()" class="w-20 h-20 rounded-xl border-2 border-dashed border-white/20 hover:border-indigo-400 hover:bg-indigo-500/10 flex flex-col items-center justify-center text-slate-400 hover:text-white transition group">
      <i data-lucide="plus" class="w-5 h-5 ${isVideo ? 'text-indigo-400' : 'text-purple-400'} group-hover:scale-110 transition"></i>
      <span class="text-[10px] mt-1 font-semibold">Thêm ảnh</span>
    </button>
  `;

  lucide.createIcons();
}

function removeSingleImage(type, id, event) {
  if (event) event.stopPropagation();
  if (type === 'video') {
    state.videoImages = state.videoImages.filter(img => img.id !== id);
  } else {
    state.imageFiles = state.imageFiles.filter(img => img.id !== id);
  }
  renderImageThumbnails(type);
}

function clearAllImages(type, event) {
  if (event) event.stopPropagation();
  if (type === 'video') {
    state.videoImages = [];
  } else {
    state.imageFiles = [];
  }
  renderImageThumbnails(type);
}

// Backward-compatible removeImage
function removeImage(type) {
  clearAllImages(type);
}

// Toast Notification
function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = 'glass-card border border-indigo-500/40 text-white shadow-2xl px-4 py-3 rounded-xl flex items-center space-x-2.5 text-xs font-semibold backdrop-blur-md transition-all duration-300 transform translate-y-2 opacity-0 pointer-events-auto bg-surface-800/95';
  toast.innerHTML = `
    <span class="w-6 h-6 rounded-lg bg-indigo-500/20 text-indigo-400 flex items-center justify-center shrink-0">
      <i data-lucide="clipboard-check" class="w-4 h-4"></i>
    </span>
    <span class="text-slate-100">${message}</span>
  `;
  container.appendChild(toast);
  lucide.createIcons();

  requestAnimationFrame(() => {
    toast.classList.remove('translate-y-2', 'opacity-0');
  });

  setTimeout(() => {
    toast.classList.add('opacity-0', 'translate-y-2');
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}

// Global Ctrl + V Image Paste Handler (Supports continuous multi-image pasting)
window.addEventListener('paste', handleGlobalPaste);

function handleGlobalPaste(event) {
  const items = (event.clipboardData || event.originalEvent?.clipboardData)?.items;
  if (!items) return;

  const files = [];
  for (const item of items) {
    if (item.type && item.type.startsWith('image/')) {
      const file = item.getAsFile();
      if (file) files.push(file);
    }
  }

  if (files.length === 0) return;

  event.preventDefault();

  let targetType = 'video';
  if (state.currentTab === 'image') {
    targetType = 'image';
  } else if (state.currentTab === 'video') {
    targetType = 'video';
  } else {
    targetType = 'video';
    switchTab('video');
  }

  // Flash target dropzone border
  const dropzone = document.getElementById(`${targetType}-dropzone`);
  if (dropzone) {
    dropzone.classList.add('border-indigo-400', 'bg-indigo-500/20');
    setTimeout(() => {
      dropzone.classList.remove('border-indigo-400', 'bg-indigo-500/20');
    }, 1000);
  }

  uploadReferenceImages(files, targetType);
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
      image_paths: state.videoImages.length > 0 ? state.videoImages.map(img => img.local_path) : (state.videoImageFile ? [state.videoImageFile] : []),
      aspect_ratio: state.videoRatio,
      duration: state.videoDuration || 5,
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
        state.storyboard = [{ scene: 1, url: videoUrl, prompt: prompt, duration: state.videoDuration || 5 }];
      } else {
        state.storyboard.push({
          scene: state.storyboard.length + 1,
          url: videoUrl,
          prompt: prompt,
          duration: state.videoDuration || 5,
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

      // Cập nhật lại Quota & Token live ngay sau khi tạo video xong
      checkAuthStatus(true).catch(e => console.warn('Cập nhật quota thất bại:', e));
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

let draggedSceneIndex = null;

function deleteScene(index, event) {
  if (event) event.stopPropagation();
  const item = state.storyboard[index];
  if (!item) return;

  const desc = item.prompt ? (item.prompt.length > 35 ? item.prompt.substring(0, 35) + '...' : item.prompt) : '';
  const confirmed = confirm(`Bạn có chắc chắn muốn xóa Cảnh #${item.scene} ${desc ? '("' + desc + '") ' : ''}khỏi chuỗi phim không?`);
  if (!confirmed) return;

  state.storyboard.splice(index, 1);
  state.storyboard.forEach((s, i) => {
    s.scene = i + 1;
  });

  if (state.storyboard.length === 0) {
    resetSequentialChain();
  } else {
    // Switch player to preview nearest remaining scene
    const nextIdx = Math.min(index, state.storyboard.length - 1);
    const nextItem = state.storyboard[nextIdx];
    const player = document.getElementById('video-player');
    if (player && nextItem) {
      player.src = nextItem.url;
      const dl = document.getElementById('video-download-btn');
      if (dl) dl.href = nextItem.url;
    }
  }
  renderStoryboard();
}

function moveScene(index, delta, event) {
  if (event) event.stopPropagation();
  const newIndex = index + delta;
  if (newIndex < 0 || newIndex >= state.storyboard.length) return;

  const item = state.storyboard.splice(index, 1)[0];
  state.storyboard.splice(newIndex, 0, item);
  state.storyboard.forEach((s, i) => {
    s.scene = i + 1;
  });
  renderStoryboard();
}

function handleSceneDragStart(e, index) {
  draggedSceneIndex = index;
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', index);
  e.currentTarget.classList.add('opacity-40', 'scale-95');
}

function handleSceneDragOver(e) {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  const card = e.currentTarget;
  card.classList.add('border-cyan-400', 'ring-2', 'ring-cyan-400/50');
}

function handleSceneDragLeave(e) {
  e.currentTarget.classList.remove('border-cyan-400', 'ring-2', 'ring-cyan-400/50');
}

function handleSceneDrop(e, targetIndex) {
  e.preventDefault();
  e.currentTarget.classList.remove('border-cyan-400', 'ring-2', 'ring-cyan-400/50');
  if (draggedSceneIndex === null || draggedSceneIndex === targetIndex) return;

  const item = state.storyboard.splice(draggedSceneIndex, 1)[0];
  state.storyboard.splice(targetIndex, 0, item);
  state.storyboard.forEach((s, i) => {
    s.scene = i + 1;
  });
  draggedSceneIndex = null;
  renderStoryboard();
}

function handleSceneDragEnd(e) {
  draggedSceneIndex = null;
  document.querySelectorAll('.storyboard-card').forEach(el => {
    el.classList.remove('opacity-40', 'scale-95', 'border-cyan-400', 'ring-2', 'ring-cyan-400/50');
  });
}

function previewSceneVideo(index) {
  const item = state.storyboard[index];
  if (!item) return;
  const player = document.getElementById('video-player');
  if (player) {
    player.src = item.url;
    player.play().catch(() => {});
  }
  const dl = document.getElementById('video-download-btn');
  if (dl) dl.href = item.url;
}

function triggerDownload(url, filename) {
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

async function mergeAndDownloadStoryboard() {
  if (!state.storyboard || state.storyboard.length === 0) {
    alert('Không có phân cảnh nào trong chuỗi Storyboard để ghép.');
    return;
  }

  const btn = document.getElementById('btn-merge-storyboard');
  const btnText = document.getElementById('merge-btn-text');
  const originalText = btnText ? btnText.innerText : 'Ghép & Tải Toàn Bộ Phim';

  // If only 1 scene, download it directly
  if (state.storyboard.length === 1) {
    const singleUrl = state.storyboard[0].url;
    triggerDownload(singleUrl, `HaiAI_Scene_1_${Date.now()}.mp4`);
    return;
  }

  try {
    if (btn) btn.disabled = true;
    if (btnText) btnText.innerText = `Đang ghép ${state.storyboard.length} cảnh...`;

    const res = await fetch('/api/video/merge', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        video_urls: state.storyboard.map(s => s.url),
      }),
    });

    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || 'Không thể ghép video.');
    }

    // Set player to preview full merged movie
    const player = document.getElementById('video-player');
    if (player && data.url) {
      player.src = data.url;
      player.play().catch(() => {});
      const dl = document.getElementById('video-download-btn');
      if (dl) dl.href = data.url;
    }

    // Automatically trigger browser download
    const filename = data.filename || `HaiAI_FullMovie_${state.storyboard.length}Scenes_${Date.now()}.mp4`;
    triggerDownload(data.url, filename);

    alert(`🎬 Đã ghép thành công ${state.storyboard.length} phân cảnh thành 1 bộ phim hoàn chỉnh!\nTrình phát đang chiếu phim ghép và file đang được tự động tải về.`);
  } catch (err) {
    alert('Lỗi ghép phim: ' + err.message);
  } finally {
    if (btn) btn.disabled = false;
    if (btnText) btnText.innerText = originalText;
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
  if (countEl) countEl.innerText = `· ${state.storyboard.length} cảnh liên kết`;
  list.innerHTML = '';

  state.storyboard.forEach((item, index) => {
    const card = document.createElement('div');
    card.draggable = true;
    card.dataset.index = index;
    card.ondragstart = (e) => handleSceneDragStart(e, index);
    card.ondragover = (e) => handleSceneDragOver(e);
    card.ondragleave = (e) => handleSceneDragLeave(e);
    card.ondrop = (e) => handleSceneDrop(e, index);
    card.ondragend = (e) => handleSceneDragEnd(e);

    const isFirst = index === 0;
    const isLast = index === state.storyboard.length - 1;

    card.className = 'storyboard-card shrink-0 w-52 p-2.5 rounded-xl bg-surface-900 hover:bg-surface-800 border border-cyan-500/30 text-left space-y-2 transition cursor-grab active:cursor-grabbing select-none group relative shadow-md';
    card.innerHTML = `
      <div class="flex items-center justify-between">
        <div class="flex items-center space-x-1.5">
          <span class="text-[10px] font-extrabold uppercase px-2 py-0.5 rounded bg-cyan-500/20 text-cyan-300">CẢNH #${item.scene}</span>
          ${item.duration ? `<span class="text-[9px] font-semibold px-1.5 py-0.5 rounded bg-surface-800 text-cyan-300 border border-white/10">${item.duration}s</span>` : ''}
        </div>
        <div class="flex items-center space-x-1">
          ${!isFirst ? `
            <button type="button" title="Chuyển sang trái" onclick="moveScene(${index}, -1, event)" class="p-1 rounded hover:bg-surface-700 text-slate-400 hover:text-white transition">
              <i data-lucide="chevron-left" class="w-3.5 h-3.5"></i>
            </button>
          ` : ''}
          ${!isLast ? `
            <button type="button" title="Chuyển sang phải" onclick="moveScene(${index}, 1, event)" class="p-1 rounded hover:bg-surface-700 text-slate-400 hover:text-white transition">
              <i data-lucide="chevron-right" class="w-3.5 h-3.5"></i>
            </button>
          ` : ''}
          <button type="button" title="Xóa phân cảnh này" onclick="deleteScene(${index}, event)" class="p-1 rounded hover:bg-rose-500/20 text-slate-400 hover:text-rose-400 transition">
            <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
          </button>
        </div>
      </div>
      <div onclick="previewSceneVideo(${index})" class="cursor-pointer space-y-1">
        <p class="text-[11px] text-slate-300 line-clamp-2 leading-tight">${escapeHtml(item.prompt || 'Không có mô tả')}</p>
        <div class="flex items-center space-x-1 text-[10px] text-cyan-400 group-hover:text-cyan-300">
          <i data-lucide="play" class="w-3 h-3 fill-current"></i>
          <span>Bấm để xem thử</span>
        </div>
      </div>
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
      image_paths: state.imageFiles.length > 0 ? state.imageFiles.map(img => img.local_path) : (state.imageFile ? [state.imageFile] : []),
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

      // Cập nhật lại Quota & Token live ngay sau khi tạo ảnh xong
      checkAuthStatus(true).catch(e => console.warn('Cập nhật quota thất bại:', e));
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
    <div class="bg-gradient-to-r from-sky-500 to-blue-600 rounded-2xl rounded-tr-none p-3.5 max-w-xl text-sm text-white shadow shadow-sky-600/25">
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
    <div class="w-8 h-8 rounded-xl bg-sky-500/20 text-sky-400 flex items-center justify-center shrink-0 border border-sky-400/30">
      <i data-lucide="sparkles" class="w-4 h-4"></i>
    </div>
    <div class="bg-surface-800 border border-white/[0.06] rounded-2xl rounded-tl-none p-3.5 max-w-xl text-sm text-slate-400 flex items-center space-x-2">
      <span class="w-2 h-2 rounded-full bg-sky-400 animate-ping"></span>
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
      btn.className = 'gfilter-btn px-3 py-1.5 rounded-lg text-xs font-semibold bg-sky-600 text-white shadow-sm shadow-sky-600/30';
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
  card.className = 'glass-card rounded-2xl overflow-hidden shadow-lg group hover:border-sky-400/40 transition flex flex-col justify-between';

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
          item.type === 'video' ? 'bg-sky-500/20 text-sky-300 border border-sky-500/30' : 'bg-teal-500/20 text-teal-300 border border-teal-500/30'
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
