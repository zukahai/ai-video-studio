// H-AI Studio — Frontend State & Logic
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
  storyboard: [], // [{ scene: 1, url: '/outputs/...', prompt: '...', duration: 5 }]
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
  // Dashboard Lazy Scroll state
  dashboardFilter: 'all',
  dashboardRenderedCount: 0,
  dashboardPageSize: 8,
  dashboardLoadingMore: false,
};

const TAB_TITLES = {
  dashboard: 'Tổng quan hệ thống (Dashboard)',
  video: 'H-AI Video Studio — Sản xuất & Nối tiếp Cảnh quay',
  movie: 'H-AI Movie Studio — Tự động Đạo diễn & Sản xuất Phim',
  image: 'H-AI Image Studio — Sáng tạo Hình ảnh 8K',
  chat: 'Trợ lý Đạo diễn & Biên kịch H-AI',
  gallery: 'Thư viện Tác phẩm H-AI',
};

document.addEventListener('DOMContentLoaded', () => {
  // 1. Restore storyboard cache from localStorage
  const hasCachedStoryboard = restoreStoryboardCache();

  // 2. Restore active tab, mode, ratio, duration, style from URL query params
  restoreFromUrlParams();

  // 3. If storyboard was restored, render it and prepare next scene view
  if (hasCachedStoryboard && state.storyboard.length > 0) {
    renderStoryboard();
    const lastItem = state.storyboard[state.storyboard.length - 1];
    const player = document.getElementById('video-player');
    if (player && lastItem && lastItem.url) {
      player.src = lastItem.url;
      document.getElementById('video-download-btn').href = lastItem.url;
      document.getElementById('video-idle-state')?.classList.add('hidden');
      document.getElementById('video-result-container')?.classList.remove('hidden');
    }
    // If user is on video tab or entered with seq mode, keep continuity ready
    if (state.isContinuation) {
      setVideoMode(true, false);
    }
  }

  checkAuthStatus();
  setupDropzones();
  setupDashboardLazyScroll();
  restoreMovieStateCache();
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
    localStorage.setItem('h_ai_last_tab', state.currentTab);
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
      const savedTab = localStorage.getItem('h_ai_last_tab') || localStorage.getItem('haiai_last_tab');
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
    const duration = parseInt(params.get('duration') || params.get('sec') || localStorage.getItem('h_ai_duration') || localStorage.getItem('haiai_duration') || '5', 10);
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
  if (tabId === 'movie') {
    renderMovieCharacters();
    renderMovieScenes();
    renderCharacterLegend();
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

// Auth Status & UI updater
function applyAuthData(data) {
  if (!data) return;
  const dot = document.getElementById('status-dot');
  const text = document.getElementById('status-text');
  const btnText = document.getElementById('auth-btn-text');

  state.authenticated = !!(data.authenticated && data.is_alive);
  state.email = data.email || null;
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

  if (dot && text && btnText) {
    if (state.authenticated) {
      dot.className = 'w-2.5 h-2.5 rounded-full bg-emerald-400 shadow-sm shadow-emerald-400';
      text.innerText = data.email || 'Đã kết nối H-AI Cloud';
      text.className = 'text-xs font-bold text-emerald-300 truncate';
      btnText.innerText = 'Đổi tài khoản';
    } else {
      dot.className = 'w-2.5 h-2.5 rounded-full bg-amber-400 animate-pulse';
      text.innerText = 'Chưa kết nối tài khoản';
      text.className = 'text-xs font-semibold text-amber-300 truncate';
      btnText.innerText = 'Đăng nhập OTP';
    }
  }
}

async function checkAuthStatus(force = false) {
  // 1. Instant cache hydrate to eliminate any perceived loading delay
  try {
    const cachedRaw = localStorage.getItem('h_ai_auth_cache');
    if (cachedRaw) {
      const cachedData = JSON.parse(cachedRaw);
      applyAuthData(cachedData);
    }
  } catch (e) {
    console.warn('Lỗi đọc auth cache:', e);
  }

  // 2. Refresh live data from server asynchronously
  try {
    const url = force ? '/api/auth/status?force=true' : '/api/auth/status';
    const res = await fetch(url);
    const data = await res.json();
    if (data) {
      try {
        localStorage.setItem('h_ai_auth_cache', JSON.stringify(data));
      } catch (e) {}
      applyAuthData(data);
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

    showModalMsg('Kết nối H-AI thành công!', false);
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


// ==================== STORYBOARD LOCAL STORAGE CACHE ====================
function saveStoryboardCache() {
  try {
    const data = {
      storyboard: state.storyboard || [],
      sessionId: state.videoSessionId || null,
      updatedAt: Date.now(),
    };
    localStorage.setItem('h_ai_storyboard_cache', JSON.stringify(data));
  } catch (err) {
    console.warn('Lỗi lưu storyboard cache:', err);
  }
}

function restoreStoryboardCache() {
  try {
    const raw = localStorage.getItem('h_ai_storyboard_cache');
    if (!raw) return false;
    const data = JSON.parse(raw);
    if (data && Array.isArray(data.storyboard) && data.storyboard.length > 0) {
      state.storyboard = data.storyboard;
      state.videoSessionId = data.sessionId || null;
      state.sceneIndex = state.storyboard.length + 1;
      return true;
    }
  } catch (err) {
    console.warn('Lỗi đọc storyboard cache:', err);
  }
  return false;
}

// User requested button: "Làm sạch các phân cảnh" -> resets storyboard and allows creating scene 1 fresh
function clearStoryboardScenes() {
  if (state.storyboard && state.storyboard.length > 0) {
    const count = state.storyboard.length;
    const confirmed = confirm(`Bạn có chắc chắn muốn làm sạch toàn bộ ${count} phân cảnh đã lưu và bắt đầu lại từ Cảnh #1 không?`);
    if (!confirmed) return;
  }

  state.storyboard = [];
  state.videoSessionId = null;
  state.sceneIndex = 1;

  try {
    localStorage.removeItem('h_ai_storyboard_cache');
  } catch (e) {}

  // Reset video player to idle state
  const player = document.getElementById('video-player');
  if (player) {
    player.pause();
    player.src = '';
  }
  document.getElementById('video-result-container')?.classList.add('hidden');
  document.getElementById('video-idle-state')?.classList.remove('hidden');

  renderStoryboard();
  setVideoMode(true); // Switch to Scene #1 creation mode

  const promptInput = document.getElementById('video-prompt');
  if (promptInput) {
    promptInput.value = '';
    promptInput.focus();
  }
}

function resetSequentialChain() {
  clearStoryboardScenes();
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

  localStorage.setItem('h_ai_duration', seconds);

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
  if (!styleName) return;

  // Normalize style name (handles URL decode and partial words)
  const norm = styleName.toLowerCase().trim();
  let matchedStyle = styleName;

  if (norm.includes('hoạt hình') || norm.includes('anime') || norm.includes('pixar')) {
    matchedStyle = 'Hoạt hình 3D Pixar / Anime';
  } else if (norm.includes('điện ảnh') || norm.includes('hollywood')) {
    matchedStyle = 'Điện ảnh 4K Hollywood';
  } else if (norm.includes('siêu thực') || norm.includes('real')) {
    matchedStyle = 'Siêu thực tế Photorealistic';
  } else if (norm.includes('cyber') || norm.includes('neon')) {
    matchedStyle = 'Cyberpunk Sci-Fi Neon';
  } else if (norm.includes('tvc') || norm.includes('quảng cáo')) {
    matchedStyle = 'TVC Quảng cáo thương mại';
  }

  state.videoStyle = matchedStyle;

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
    if (name === matchedStyle) {
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
      document.getElementById('video-progress-text').innerText = 'H-AI Director đang thiết lập góc máy & đồng nhất nhân vật...';
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
      saveStoryboardCache();
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
  saveStoryboardCache();

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
  saveStoryboardCache();
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
  saveStoryboardCache();
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
    triggerDownload(singleUrl, `H-AI_Scene_1_${Date.now()}.mp4`);
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
    const filename = data.filename || `H-AI_FullMovie_${state.storyboard.length}Scenes_${Date.now()}.mp4`;
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
      <span>H-AI đang biên kịch...</span>
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

// ==================== DASHBOARD LAZY SCROLL FEED ====================
let dashboardObserver = null;

function filterDashboard(filterType) {
  state.dashboardFilter = filterType;
  ['all', 'video', 'image'].forEach(f => {
    const btn = document.getElementById(`dbfilter-${f}`);
    if (!btn) return;
    if (f === filterType) {
      btn.className = 'dbfilter-btn px-3 py-1.5 rounded-lg text-xs font-semibold bg-sky-600 text-white shadow-sm shadow-sky-600/30 transition';
    } else {
      btn.className = 'dbfilter-btn px-3 py-1.5 rounded-lg text-xs font-semibold bg-surface-800 text-slate-300 hover:text-white transition';
    }
  });
  renderDashboardLazy(true);
}

function getFilteredDashboardItems() {
  return state.galleryItems.filter(it =>
    state.dashboardFilter === 'all' ? true : it.type === state.dashboardFilter
  );
}

function renderDashboardLazy(reset = false) {
  const grid = document.getElementById('dashboard-recent-grid');
  const counter = document.getElementById('dashboard-items-counter');
  const spinner = document.getElementById('dashboard-lazy-spinner');
  const endEl = document.getElementById('dashboard-lazy-end');
  if (!grid) return;

  const items = getFilteredDashboardItems();

  if (counter) {
    counter.innerText = `· ${items.length} tác phẩm`;
  }

  if (reset) {
    state.dashboardRenderedCount = 0;
    grid.innerHTML = '';
  }

  if (items.length === 0) {
    grid.innerHTML = `
      <div class="col-span-full py-12 text-center text-slate-500 text-xs space-y-2">
        <i data-lucide="film" class="w-8 h-8 mx-auto text-slate-600"></i>
        <p>Chưa có sản phẩm nào trong danh mục này.</p>
      </div>
    `;
    if (spinner) spinner.classList.add('hidden');
    if (endEl) endEl.classList.add('hidden');
    lucide.createIcons();
    return;
  }

  const nextCount = Math.min(items.length, state.dashboardRenderedCount + state.dashboardPageSize);
  const chunk = items.slice(state.dashboardRenderedCount, nextCount);

  chunk.forEach(item => {
    grid.appendChild(createMediaCard(item));
  });

  state.dashboardRenderedCount = nextCount;
  lucide.createIcons();

  if (state.dashboardRenderedCount >= items.length) {
    if (spinner) spinner.classList.add('hidden');
    if (endEl) endEl.classList.remove('hidden');
  } else {
    if (endEl) endEl.classList.add('hidden');
    if (spinner) spinner.classList.add('hidden');
  }
}

function loadMoreDashboardItems() {
  if (state.dashboardLoadingMore) return;
  const items = getFilteredDashboardItems();
  if (state.dashboardRenderedCount >= items.length) return;

  state.dashboardLoadingMore = true;
  const spinner = document.getElementById('dashboard-lazy-spinner');
  if (spinner) spinner.classList.remove('hidden');

  setTimeout(() => {
    renderDashboardLazy(false);
    state.dashboardLoadingMore = false;
  }, 250);
}

function setupDashboardLazyScroll() {
  const sentinel = document.getElementById('dashboard-lazy-sentinel');
  const scrollContainer = document.querySelector('main');

  if (sentinel && 'IntersectionObserver' in window) {
    dashboardObserver = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting && state.currentTab === 'dashboard') {
          loadMoreDashboardItems();
        }
      });
    }, {
      root: scrollContainer,
      rootMargin: '120px',
      threshold: 0.1,
    });
    dashboardObserver.observe(sentinel);
  }

  // Fallback scroll listener on main container
  if (scrollContainer) {
    scrollContainer.addEventListener('scroll', () => {
      if (state.currentTab !== 'dashboard') return;
      const threshold = 180;
      if (scrollContainer.scrollHeight - scrollContainer.scrollTop - scrollContainer.clientHeight <= threshold) {
        loadMoreDashboardItems();
      }
    });
  }
}

function renderDashboardRecent() {
  renderDashboardLazy(true);
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
          ${item.type === 'video' ? 'H-AI Video' : 'H-AI Image'}
        </span>
        <div class="flex items-center space-x-1.5">
          ${item.type === 'video' ? `
            <button onclick="continueFromVideoUrl('${item.url}')" type="button" title="Tạo cảnh nối tiếp từ video này" class="px-2 py-1 rounded-lg bg-cyan-500/15 hover:bg-cyan-500/25 text-cyan-300 border border-cyan-500/30 text-xs font-semibold flex items-center space-x-1 transition">
              <i data-lucide="link-2" class="w-3 h-3"></i>
              <span>Nối tiếp</span>
            </button>
          ` : ''}
          <a href="${item.url}" download class="px-2.5 py-1 rounded-lg bg-surface-700 hover:bg-surface-600 text-slate-200 text-xs font-medium flex items-center space-x-1 transition">
            <i data-lucide="download" class="w-3 h-3"></i>
            <span>Tải về</span>
          </a>
        </div>
      </div>
    </div>
  `;
  return card;
}


function continueFromVideoUrl(url) {
  switchTab('video');
  if (state.storyboard.length === 0) {
    state.storyboard = [{ scene: 1, url: url, prompt: 'Cảnh khởi đầu', duration: state.videoDuration || 5 }];
    saveStoryboardCache();
  }
  const player = document.getElementById('video-player');
  if (player) {
    player.src = url;
    document.getElementById('video-download-btn').href = url;
    document.getElementById('video-idle-state')?.classList.add('hidden');
    document.getElementById('video-result-container')?.classList.remove('hidden');
  }
  renderStoryboard();
  setVideoMode(true);
  const promptEl = document.getElementById('video-prompt');
  if (promptEl) {
    promptEl.value = '';
    promptEl.focus();
  }
}

function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}


// ============================================================================
// ==================== H-AI AUTO MOVIE STUDIO ENGINE =========================
// ============================================================================

const MOVIE_PALETTE = [
  '#ef4444', // Red (e.g. Bà lão)
  '#3b82f6', // Electric Blue (e.g. Hiệp sĩ)
  '#10b981', // Emerald Green
  '#f59e0b', // Amber
  '#8b5cf6', // Purple
  '#ec4899', // Pink
  '#06b6d4', // Cyan
  '#f97316', // Orange
  '#14b8a6', // Teal
  '#a855f7', // Violet
];

const movieState = {
  title: 'Kịch bản Phim Mới',
  logline: '',
  script: '',
  stylePreset: 'Điện ảnh 4K Hollywood',
  aspectRatio: '16:9',
  targetScenes: 4,
  characters: [], // [{ id, name, description, color, images: [{ id, local_path, url, name }] }]
  scenes: [], // [{ scene_index, situation, dialogue, duration, characters_involved: [], prompt, status: 'idle'|'generating'|'done', video_url: null }]
  sessionId: null,
  isGenerating: false,
  currentGeneratingIndex: -1,
  shouldStop: false,
  // Cinema Theater
  theaterCurrentIndex: 0,
  theaterIsPlaying: false,
};

// ==================== CACHING & RESTORATION ====================
function saveMovieStateCache() {
  try {
    const data = {
      title: movieState.title,
      logline: movieState.logline,
      script: movieState.script,
      stylePreset: movieState.stylePreset,
      aspectRatio: movieState.aspectRatio,
      targetScenes: movieState.targetScenes,
      characters: movieState.characters,
      scenes: movieState.scenes,
      sessionId: movieState.sessionId,
      updatedAt: Date.now(),
    };
    localStorage.setItem('h_ai_movie_studio_cache', JSON.stringify(data));
  } catch (err) {
    console.warn('Lỗi lưu cache tạo phim:', err);
  }
}

function restoreMovieStateCache() {
  try {
    const raw = localStorage.getItem('h_ai_movie_studio_cache');
    if (!raw) return false;
    const data = JSON.parse(raw);
    if (!data) return false;

    movieState.title = data.title || 'Kịch bản Phim Mới';
    movieState.logline = data.logline || '';
    movieState.script = data.script || '';
    movieState.stylePreset = data.stylePreset || 'Điện ảnh 4K Hollywood';
    movieState.aspectRatio = data.aspectRatio || '16:9';
    movieState.targetScenes = data.targetScenes || 4;
    movieState.characters = Array.isArray(data.characters) ? data.characters : [];
    movieState.scenes = Array.isArray(data.scenes) ? data.scenes : [];
    movieState.sessionId = data.sessionId || null;

    // Restore UI Inputs
    const scriptInput = document.getElementById('movie-script-input');
    if (scriptInput && movieState.script) scriptInput.value = movieState.script;

    const styleSelect = document.getElementById('movie-style-select');
    if (styleSelect && movieState.stylePreset) styleSelect.value = movieState.stylePreset;

    const ratioSelect = document.getElementById('movie-ratio-select');
    if (ratioSelect && movieState.aspectRatio) ratioSelect.value = movieState.aspectRatio;

    const scenesTarget = document.getElementById('movie-scenes-target');
    if (scenesTarget && movieState.targetScenes) scenesTarget.value = String(movieState.targetScenes);

    renderMovieCharacters();
    renderMovieScenes();
    renderCharacterLegend();
    return true;
  } catch (err) {
    console.warn('Lỗi đọc cache tạo phim:', err);
  }
  return false;
}

// Sample Script
function loadSampleScript() {
  const sample = `Ngày xửa ngày xưa, ở bìa rừng tuyết trắng có một Bà lão sống trong túp lều gỗ. Một đêm bão tuyết mù mịt, Bà lão đang ngồi sưởi ấm bên bếp lửa thì có tiếng gõ cửa dồn dập.
Khi Bà lão mở cửa, một Chàng hiệp sĩ bị thương nặng ngã vào hiên nhà, trên tay ôm chặt một Quả trứng rồng hoàng kim phát sáng rực rỡ.
Chàng hiệp sĩ cầu xin Bà lão bảo vệ quả trứng trước Đội quân bóng tối. Bà lão đưa Chàng hiệp sĩ vào nhà, lấy thảo dược chữa lành vết thương và cùng nhau chuẩn bị chiến đấu.`;

  const scriptInput = document.getElementById('movie-script-input');
  if (scriptInput) {
    scriptInput.value = sample;
    movieState.script = sample;
    saveMovieStateCache();
  }
}

function onMovieStyleChange(val) {
  movieState.stylePreset = val;
  saveMovieStateCache();
}

function onMovieRatioChange(val) {
  movieState.aspectRatio = val;
  saveMovieStateCache();
}

// ==================== SCRIPT ANALYSIS & BREAKDOWN ====================
async function analyzeAndBreakdownScript() {
  const scriptInput = document.getElementById('movie-script-input');
  const script = scriptInput ? scriptInput.value.trim() : '';

  if (!script) {
    alert('Vui lòng nhập cốt truyện hoặc kịch bản bạn muốn làm phim.');
    if (scriptInput) scriptInput.focus();
    return;
  }

  const btn = document.getElementById('btn-breakdown-script');
  const btnText = document.getElementById('btn-breakdown-text');
  const originalText = btnText ? btnText.innerText : 'Phân Tích Kịch Bản';

  try {
    if (btn) btn.disabled = true;
    if (btnText) btnText.innerText = 'Đang phân tích kịch bản & nhân vật...';

    const style = document.getElementById('movie-style-select')?.value || 'Điện ảnh 4K Hollywood';
    const ratio = document.getElementById('movie-ratio-select')?.value || '16:9';
    const targetScenes = parseInt(document.getElementById('movie-scenes-target')?.value || '4', 10);

    movieState.script = script;
    movieState.stylePreset = style;
    movieState.aspectRatio = ratio;
    movieState.targetScenes = targetScenes;

    const res = await fetch('/api/movie/breakdown', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        script: script,
        style_preset: style,
        aspect_ratio: ratio,
        target_scenes: targetScenes,
      }),
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Không thể phân tích kịch bản.');

    const result = data.data || {};
    movieState.title = result.title || 'Phim Điện Ảnh H-AI';
    movieState.logline = result.logline || '';

    // Assign characters with distinct colors
    const characters = Array.isArray(result.characters) ? result.characters : [];
    characters.forEach((char, idx) => {
      if (!char.id) char.id = `char_${idx + 1}`;
      if (!char.color) char.color = MOVIE_PALETTE[idx % MOVIE_PALETTE.length];
      if (!Array.isArray(char.images)) char.images = [];
    });
    movieState.characters = characters;

    // Assign scenes
    const scenes = Array.isArray(result.scenes) ? result.scenes : [];
    scenes.forEach((sc, idx) => {
      sc.scene_index = idx + 1;
      sc.status = 'idle';
      sc.video_url = null;
      if (!sc.duration) sc.duration = 8;
      if (!Array.isArray(sc.characters_involved)) sc.characters_involved = [];
    });
    movieState.scenes = scenes;
    movieState.sessionId = null; // Fresh movie session

    saveMovieStateCache();
    renderMovieCharacters();
    renderMovieScenes();
    renderCharacterLegend();

    alert(`🎬 Phân tích thành công!
Đã trích xuất ${characters.length} nhân vật và ${scenes.length} phân cảnh chi tiết.
Bạn có thể tải ảnh tham chiếu cho nhân vật hoặc bấm [Bắt Đầu Tạo Phim Tự Động] ngay!`);
  } catch (err) {
    alert('Lỗi phân tích kịch bản: ' + err.message);
  } finally {
    if (btn) btn.disabled = false;
    if (btnText) btnText.innerText = originalText;
    lucide.createIcons();
  }
}

// ==================== CHARACTER MANAGEMENT ====================
function renderCharacterLegend() {
  const container = document.getElementById('movie-characters-legend');
  if (!container) return;

  if (movieState.characters.length === 0) {
    container.innerHTML = `<span class="text-[11px] text-slate-500 italic">Chưa có nhân vật nào. Nhập kịch bản hoặc bấm "+ Thêm nhân vật".</span>`;
    return;
  }

  container.innerHTML = `
    <span class="text-[11px] font-semibold text-slate-400 mr-1 flex items-center shrink-0">
      <i data-lucide="tag" class="w-3 h-3 mr-1 text-cyan-400"></i> Bảng màu nhân vật:
    </span>
  `;

  movieState.characters.forEach(char => {
    const pill = document.createElement('span');
    pill.className = 'inline-flex items-center space-x-1.5 px-2 py-0.5 rounded-full text-[11px] font-bold border transition';
    pill.style.color = char.color;
    pill.style.backgroundColor = `${char.color}18`;
    pill.style.borderColor = `${char.color}44`;
    pill.innerHTML = `
      <span class="w-2 h-2 rounded-full" style="background-color: ${char.color}"></span>
      <span>${escapeHtml(char.name)}</span>
    `;
    container.appendChild(pill);
  });
  lucide.createIcons();
}

function renderMovieCharacters() {
  const container = document.getElementById('movie-characters-list');
  const countBadge = document.getElementById('movie-char-count-badge');
  if (!container) return;

  if (countBadge) countBadge.innerText = `${movieState.characters.length} nhân vật`;

  if (movieState.characters.length === 0) {
    container.innerHTML = `
      <div class="py-8 text-center text-slate-500 text-xs space-y-1">
        <i data-lucide="users" class="w-8 h-8 mx-auto text-slate-600"></i>
        <p>Chưa có nhân vật nào. Bấm <b>Phân Tích Kịch Bản</b> hoặc <b>+ Thêm nhân vật</b>.</p>
      </div>
    `;
    lucide.createIcons();
    return;
  }

  container.innerHTML = '';

  movieState.characters.forEach((char, idx) => {
    const card = document.createElement('div');
    card.className = 'p-3.5 rounded-xl bg-surface-900/90 border border-white/[0.08] hover:border-cyan-500/30 transition space-y-3';
    card.dataset.charId = char.id;

    // Render character image thumbnails
    let imagesHtml = '';
    if (char.images && char.images.length > 0) {
      imagesHtml = `
        <div class="flex flex-wrap gap-2 pt-1 pb-1">
          ${char.images.map((img, imgIdx) => `
            <div class="relative w-12 h-12 rounded-lg overflow-hidden border border-white/20 group">
              <img src="${img.url}" class="w-full h-full object-cover">
              <button type="button" onclick="deleteCharacterImage('${char.id}', ${imgIdx}, event)" class="absolute inset-0 bg-black/60 text-white flex items-center justify-center opacity-0 group-hover:opacity-100 transition">
                <i data-lucide="x" class="w-3.5 h-3.5"></i>
              </button>
            </div>
          `).join('')}
        </div>
      `;
    }

    card.innerHTML = `
      <div class="flex items-center justify-between">
        <div class="flex items-center space-x-2.5 flex-1 min-w-0 mr-2">
          <!-- Color Swatch Button -->
          <div class="relative group cursor-pointer" title="Bấm để đổi màu nhân vật">
            <input type="color" value="${char.color}" onchange="changeCharacterColor('${char.id}', this.value)" class="absolute inset-0 opacity-0 cursor-pointer w-full h-full">
            <span class="w-6 h-6 rounded-full block border-2 border-white/40 shadow-sm" style="background-color: ${char.color}"></span>
          </div>

          <input type="text" value="${escapeHtml(char.name)}" oninput="updateCharacterName('${char.id}', this.value)" placeholder="Tên nhân vật..." class="bg-surface-950 border border-surface-600 focus:border-cyan-400 rounded-lg px-2.5 py-1 text-xs font-bold text-white focus:outline-none flex-1">
        </div>

        <button type="button" onclick="deleteCharacter('${char.id}')" title="Xóa nhân vật" class="p-1 rounded-lg hover:bg-rose-500/20 text-slate-400 hover:text-rose-400 transition">
          <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
        </button>
      </div>

      <div>
        <label class="block text-[10px] font-semibold text-slate-400 mb-1">Mô tả ngoại hình & phục trang (để tạo ảnh chuẩn):</label>
        <textarea rows="2" oninput="updateCharacterDesc('${char.id}', this.value)" placeholder="Chi tiết ngoại hình, độ tuổi, khuôn mặt, áo quần..." class="w-full bg-surface-950/80 border border-surface-600 focus:border-cyan-400 rounded-lg p-2 text-xs text-slate-200 focus:outline-none resize-none">${escapeHtml(char.description || '')}</textarea>
      </div>

      <!-- Reference Images & Paste Zone -->
      <div class="space-y-1.5">
        <div class="flex items-center justify-between text-[10px] text-slate-400">
          <span>Ảnh tham chiếu (${char.images ? char.images.length : 0} ảnh):</span>
          <span class="text-cyan-400 font-semibold">Ctrl + V để dán ảnh</span>
        </div>

        ${imagesHtml}

        <div id="char-dropzone-${char.id}" tabindex="0" onpaste="handleCharacterPaste(event, '${char.id}')" class="border border-dashed border-surface-600 hover:border-cyan-400 rounded-lg p-2 text-center bg-surface-950/50 hover:bg-surface-950 transition cursor-pointer flex items-center justify-between px-3">
          <input type="file" id="char-file-${char.id}" accept="image/*" multiple class="hidden" onchange="handleCharacterUpload(event, '${char.id}')">
          <div onclick="document.getElementById('char-file-${char.id}').click()" class="flex items-center space-x-2 text-left">
            <i data-lucide="image-plus" class="w-4 h-4 text-cyan-400 shrink-0"></i>
            <span class="text-[11px] text-slate-300">Bấm chọn ảnh hoặc bấm <kbd class="px-1 py-0.5 rounded bg-surface-800 text-[10px] text-cyan-300 font-mono">Ctrl+V</kbd> dán</span>
          </div>
          <button type="button" onclick="generateCharacterPortrait('${char.id}')" id="btn-gen-char-${char.id}" class="px-2 py-1 rounded bg-gradient-to-r from-teal-600 to-sky-600 hover:from-teal-500 hover:to-sky-500 text-white text-[10px] font-bold flex items-center space-x-1 shrink-0 shadow-sm transition">
            <i data-lucide="sparkles" class="w-3 h-3"></i>
            <span>AI Vẽ</span>
          </button>
        </div>
      </div>
    `;

    container.appendChild(card);
  });

  lucide.createIcons();
}

function addNewCharacter() {
  const idx = movieState.characters.length + 1;
  const newColor = MOVIE_PALETTE[(idx - 1) % MOVIE_PALETTE.length];
  movieState.characters.push({
    id: `char_${Date.now()}`,
    name: `Nhân vật #${idx}`,
    description: `Trang phục và phong thái chuẩn điện ảnh ${movieState.stylePreset}.`,
    color: newColor,
    images: [],
  });
  saveMovieStateCache();
  renderMovieCharacters();
  renderCharacterLegend();
  renderMovieScenes(); // update highlights
}

function deleteCharacter(charId) {
  const char = movieState.characters.find(c => c.id === charId);
  if (!char) return;
  if (!confirm(`Bạn có chắc chắn muốn xóa nhân vật "${char.name}" không?`)) return;

  movieState.characters = movieState.characters.filter(c => c.id !== charId);
  saveMovieStateCache();
  renderMovieCharacters();
  renderCharacterLegend();
  renderMovieScenes();
}

function updateCharacterName(charId, newName) {
  const char = movieState.characters.find(c => c.id === charId);
  if (char) {
    char.name = newName.trim() || 'Nhân vật';
    saveMovieStateCache();
    renderCharacterLegend();
    renderMovieScenes();
  }
}

function updateCharacterDesc(charId, newDesc) {
  const char = movieState.characters.find(c => c.id === charId);
  if (char) {
    char.description = newDesc;
    saveMovieStateCache();
  }
}

function changeCharacterColor(charId, newColor) {
  const char = movieState.characters.find(c => c.id === charId);
  if (char) {
    char.color = newColor;
    saveMovieStateCache();
    renderMovieCharacters();
    renderCharacterLegend();
    renderMovieScenes();
  }
}

// Character Image Upload & Paste
async function handleCharacterUpload(e, charId) {
  const files = Array.from(e.target.files || []);
  if (!files.length) return;
  await uploadFilesForCharacter(files, charId);
}

async function handleCharacterPaste(e, charId) {
  const clipboardData = e.clipboardData || window.clipboardData;
  if (!clipboardData) return;
  const items = clipboardData.items || [];
  const files = [];
  for (let i = 0; i < items.length; i++) {
    if (items[i].type.indexOf('image') !== -1) {
      const blob = items[i].getAsFile();
      if (blob) files.push(blob);
    }
  }
  if (files.length > 0) {
    e.preventDefault();
    await uploadFilesForCharacter(files, charId);
  }
}

async function uploadFilesForCharacter(files, charId) {
  const char = movieState.characters.find(c => c.id === charId);
  if (!char) return;

  for (const file of files) {
    const formData = new FormData();
    formData.append('file', file);
    try {
      const res = await fetch('/api/upload', {
        method: 'POST',
        body: formData,
      });
      const data = await res.json();
      if (res.ok && data.url) {
        if (!char.images) char.images = [];
        char.images.push({
          id: `img_${Date.now()}_${Math.random()}`,
          local_path: data.local_path,
          url: data.url,
          name: file.name,
        });
      }
    } catch (err) {
      console.warn('Lỗi tải ảnh nhân vật:', err);
    }
  }
  saveMovieStateCache();
  renderMovieCharacters();
}

function deleteCharacterImage(charId, imgIdx, e) {
  if (e) e.stopPropagation();
  const char = movieState.characters.find(c => c.id === charId);
  if (char && char.images) {
    char.images.splice(imgIdx, 1);
    saveMovieStateCache();
    renderMovieCharacters();
  }
}

// AI Generate Character Portrait Concept Art
async function generateCharacterPortrait(charId) {
  const char = movieState.characters.find(c => c.id === charId);
  if (!char) return;

  const btn = document.getElementById(`btn-gen-char-${charId}`);
  if (btn) btn.disabled = true;

  try {
    const prompt = `Chân dung nhân vật ${char.name}, ${char.description || ''}, phong cách ${movieState.stylePreset}, điện ảnh 8K, độ nét cao, ánh sáng chuyên nghiệp`;
    const res = await fetch('/api/image/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        prompt: prompt,
        aspect_ratio: '1:1',
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Không thể tạo ảnh.');

    if (data.images && data.images.length > 0) {
      const imgUrl = data.images[0];
      if (!char.images) char.images = [];
      char.images.push({
        id: `img_${Date.now()}`,
        local_path: null,
        url: imgUrl,
        name: `AI_Portrait_${char.name}.png`,
      });
      saveMovieStateCache();
      renderMovieCharacters();
    }
  } catch (err) {
    alert('Lỗi tạo ảnh nhân vật: ' + err.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ==================== CHARACTER NAME HIGHLIGHTING ====================
// User requirement: "tên mỗi nhân vật cho một màu riêng để lúc hiện ở kịch bản cho dễ nhìn, ví dụ bà lão là tên màu đỏ thì ở kịch bản từng promt mà có nhắc tới bà lão thì chữ bà lão đó cũng màu đó"
function highlightCharacterNamesInText(text) {
  if (!text) return '';
  let formatted = escapeHtml(text);

  // Sort characters by longest name first to avoid partial overlap replacement
  const sortedChars = [...movieState.characters].sort((a, b) => b.name.length - a.name.length);

  sortedChars.forEach(char => {
    if (!char.name || char.name.length < 2) return;
    const escapedName = escapeHtml(char.name).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const regex = new RegExp(`(\\b${escapedName}\\b|@${escapedName})`, 'gi');

    formatted = formatted.replace(regex, (match) => {
      return `<span class="inline-flex items-center px-1.5 py-0.5 rounded text-[11px] font-bold mx-0.5" style="color: ${char.color}; background-color: ${char.color}22; border: 1px solid ${char.color}55;">${match}</span>`;
    });
  });

  return formatted;
}

// ==================== SCENE STORYBOARD RENDERING ====================
function renderMovieScenes() {
  const container = document.getElementById('movie-scenes-container');
  const countBadge = document.getElementById('movie-scenes-count-badge');
  const durText = document.getElementById('movie-total-duration-text');
  const renderedText = document.getElementById('movie-rendered-count-text');

  if (!container) return;

  const totalScenes = movieState.scenes.length;
  const renderedCount = movieState.scenes.filter(s => s.video_url).length;
  const totalDuration = movieState.scenes.reduce((sum, s) => sum + (parseInt(s.duration, 10) || 8), 0);

  if (countBadge) countBadge.innerText = `${totalScenes} cảnh`;
  if (durText) durText.innerText = `${totalDuration}s`;
  if (renderedText) renderedText.innerText = `${renderedCount}/${totalScenes} cảnh`;

  if (totalScenes === 0) {
    container.innerHTML = `
      <div class="py-16 text-center text-slate-500 text-xs space-y-2">
        <i data-lucide="clapperboard" class="w-10 h-10 mx-auto text-slate-600"></i>
        <p class="text-sm">Chưa có phân cảnh nào.</p>
        <p class="text-slate-400">Hãy nhập kịch bản bên trái rồi bấm <b>Phân Tích Kịch Bản</b> để tạo phân cảnh tự động.</p>
      </div>
    `;
    lucide.createIcons();
    return;
  }

  container.innerHTML = '';

  movieState.scenes.forEach((scene, idx) => {
    const card = document.createElement('div');
    const isGenerating = scene.status === 'generating';
    const isDone = scene.status === 'done' || Boolean(scene.video_url);

    card.className = `p-4 rounded-2xl bg-surface-900 border transition space-y-3 relative ${
      isGenerating
        ? 'border-cyan-400 ring-2 ring-cyan-400/40 shadow-lg shadow-cyan-500/20'
        : isDone
        ? 'border-emerald-500/40 bg-surface-900/90 shadow-md'
        : 'border-white/[0.08] hover:border-cyan-500/20'
    }`;

    // Characters involved tags
    const charTags = (scene.characters_involved || []).map(name => {
      const char = movieState.characters.find(c => c.name.toLowerCase() === name.toLowerCase());
      const color = char ? char.color : '#38bdf8';
      return `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold" style="color: ${color}; background-color: ${color}22; border: 1px solid ${color}44;">@${escapeHtml(name)}</span>`;
    }).join(' ');

    // Video Player / Preview
    let mediaSection = '';
    if (scene.video_url) {
      mediaSection = `
        <div class="relative rounded-xl overflow-hidden bg-black border border-white/10 aspect-video flex items-center justify-center">
          <video src="${scene.video_url}" controls playsinline class="w-full h-full object-contain"></video>
        </div>
      `;
    } else if (isGenerating) {
      mediaSection = `
        <div class="rounded-xl overflow-hidden bg-cyan-950/30 border border-cyan-400/40 py-6 text-center space-y-2">
          <div class="w-8 h-8 border-3 border-cyan-400 border-t-transparent rounded-full animate-spin mx-auto"></div>
          <p class="text-xs font-bold text-cyan-300">Đang sản xuất cảnh này...</p>
        </div>
      `;
    }

    card.innerHTML = `
      <!-- Scene Card Header -->
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div class="flex items-center space-x-2">
          <span class="text-xs font-extrabold uppercase px-2.5 py-1 rounded-lg bg-cyan-500/20 text-cyan-300 border border-cyan-500/30">CẢNH #${scene.scene_index}</span>
          <div class="flex items-center space-x-1">
            ${charTags}
          </div>
        </div>

        <div class="flex items-center space-x-1.5">
          <!-- Duration selector (User request: 'số giây phù hợp') -->
          <select onchange="updateSceneDuration(${idx}, this.value)" class="bg-surface-950 border border-surface-600 focus:border-cyan-400 rounded-lg px-2 py-1 text-xs text-cyan-300 font-bold focus:outline-none cursor-pointer">
            <option value="5" ${scene.duration == 5 ? 'selected' : ''}>5 giây</option>
            <option value="8" ${scene.duration == 8 ? 'selected' : ''}>8 giây</option>
            <option value="10" ${scene.duration == 10 ? 'selected' : ''}>10 giây</option>
            <option value="15" ${scene.duration == 15 ? 'selected' : ''}>15 giây</option>
            <option value="20" ${scene.duration == 20 ? 'selected' : ''}>20 giây</option>
          </select>

          <!-- Reorder & Action buttons -->
          <button type="button" title="Chuyển lên" onclick="moveMovieScene(${idx}, -1)" ${idx === 0 ? 'disabled' : ''} class="p-1 rounded hover:bg-surface-800 text-slate-400 hover:text-white disabled:opacity-30 transition">
            <i data-lucide="chevron-up" class="w-4 h-4"></i>
          </button>
          <button type="button" title="Chuyển xuống" onclick="moveMovieScene(${idx}, 1)" ${idx === totalScenes - 1 ? 'disabled' : ''} class="p-1 rounded hover:bg-surface-800 text-slate-400 hover:text-white disabled:opacity-30 transition">
            <i data-lucide="chevron-down" class="w-4 h-4"></i>
          </button>
          <button type="button" title="Xóa phân cảnh" onclick="deleteMovieScene(${idx})" class="p-1 rounded hover:bg-rose-500/20 text-slate-400 hover:text-rose-400 transition">
            <i data-lucide="trash-2" class="w-4 h-4"></i>
          </button>
        </div>
      </div>

      ${mediaSection}

      <!-- Situation / Tình huống (Colored representation) -->
      <div class="space-y-1">
        <label class="text-[11px] font-semibold text-slate-400 flex items-center justify-between">
          <span>Tình huống & Bối cảnh:</span>
          <span class="text-[10px] text-cyan-400">Gõ @ để gắn thẻ nhân vật</span>
        </label>
        <div class="p-2.5 rounded-xl bg-surface-950/80 border border-white/[0.06] text-xs text-slate-200 leading-relaxed min-h-[38px] cursor-text" onclick="focusSceneEditable(this, 'situation', ${idx})">
          ${highlightCharacterNamesInText(scene.situation || 'Chưa có tình huống')}
        </div>
      </div>

      <!-- Dialogue / Lời thoại (Colored representation) -->
      <div class="space-y-1">
        <label class="text-[11px] font-semibold text-amber-300/80 flex items-center space-x-1">
          <i data-lucide="message-square" class="w-3 h-3 text-amber-400"></i>
          <span>Lời thoại / Thuyết minh:</span>
        </label>
        <div class="p-2.5 rounded-xl bg-amber-950/20 border border-amber-500/20 text-xs text-amber-200 leading-relaxed min-h-[36px] cursor-text" onclick="focusSceneEditable(this, 'dialogue', ${idx})">
          ${highlightCharacterNamesInText(scene.dialogue || 'Không có thoại')}
        </div>
      </div>

      <!-- Video Prompt -->
      <div class="space-y-1">
        <label class="text-[11px] font-semibold text-slate-400 flex items-center justify-between">
          <span>Prompt quay phim AI (Cinematic Prompt):</span>
          <span class="text-[10px] text-slate-500">Chỉnh sửa mô tả góc máy & ánh sáng</span>
        </label>
        <textarea rows="2" oninput="updateSceneField(${idx}, 'prompt', this.value)" class="mention-target w-full bg-surface-950 border border-surface-600 focus:border-cyan-400 rounded-xl p-2.5 text-xs text-slate-200 focus:outline-none resize-none leading-relaxed">${escapeHtml(scene.prompt || '')}</textarea>
      </div>

      <!-- Single Scene Action Row -->
      <div class="flex items-center justify-between pt-1 text-xs">
        <span class="text-[10px] ${isDone ? 'text-emerald-400 font-bold' : isGenerating ? 'text-cyan-400 animate-pulse font-bold' : 'text-slate-500'}">
          ${isDone ? '✓ Đã quay xong' : isGenerating ? '● Đang quay video...' : '○ Chờ sản xuất'}
        </span>

        <button type="button" onclick="generateSingleMovieScene(${idx})" class="px-3 py-1 rounded-lg bg-surface-800 hover:bg-surface-700 text-cyan-300 border border-cyan-500/30 text-xs font-semibold flex items-center space-x-1 transition cursor-pointer">
          <i data-lucide="refresh-cw" class="w-3 h-3"></i>
          <span>${isDone ? 'Quay lại cảnh này' : 'Quay cảnh này'}</span>
        </button>
      </div>
    `;

    container.appendChild(card);
  });

  lucide.createIcons();
  attachMentionListeners();
}

// Inline Editable for Situation & Dialogue with @ Autocomplete
function focusSceneEditable(containerEl, field, sceneIdx) {
  const currentVal = movieState.scenes[sceneIdx] ? (movieState.scenes[sceneIdx][field] || '') : '';
  const textarea = document.createElement('textarea');
  textarea.className = 'mention-target w-full bg-surface-950 border border-cyan-400 rounded-xl p-2.5 text-xs text-slate-100 focus:outline-none resize-none leading-relaxed';
  textarea.rows = 2;
  textarea.value = currentVal;

  textarea.onblur = () => {
    updateSceneField(sceneIdx, field, textarea.value);
    renderMovieScenes();
  };

  containerEl.replaceWith(textarea);
  textarea.focus();
  setupMentionInput(textarea);
}

function updateSceneField(idx, field, val) {
  if (movieState.scenes[idx]) {
    movieState.scenes[idx][field] = val;
    saveMovieStateCache();
  }
}

function updateSceneDuration(idx, val) {
  const num = parseInt(val, 10);
  if (movieState.scenes[idx]) {
    movieState.scenes[idx].duration = isNaN(num) ? 8 : num;
    saveMovieStateCache();
    renderMovieScenes();
  }
}

function addNewScene() {
  const nextIdx = movieState.scenes.length + 1;
  const involved = movieState.characters.length > 0 ? [movieState.characters[0].name] : [];
  movieState.scenes.push({
    scene_index: nextIdx,
    situation: `Diễn biến Cảnh #${nextIdx}...`,
    dialogue: `Lời thoại Cảnh #${nextIdx}...`,
    duration: 8,
    characters_involved: involved,
    prompt: `Cinematic 4K, ${movieState.stylePreset}, Scene ${nextIdx}. Featuring ${involved.join(', ')}, photorealistic lighting, 8k masterpiece`,
    status: 'idle',
    video_url: null,
  });
  saveMovieStateCache();
  renderMovieScenes();
}

function deleteMovieScene(idx) {
  const sc = movieState.scenes[idx];
  if (!sc) return;
  if (!confirm(`Bạn có chắc chắn muốn xóa Cảnh #${sc.scene_index} không?`)) return;

  movieState.scenes.splice(idx, 1);
  movieState.scenes.forEach((s, i) => {
    s.scene_index = i + 1;
  });
  saveMovieStateCache();
  renderMovieScenes();
}

function moveMovieScene(idx, delta) {
  const newIdx = idx + delta;
  if (newIdx < 0 || newIdx >= movieState.scenes.length) return;

  const item = movieState.scenes.splice(idx, 1)[0];
  movieState.scenes.splice(newIdx, 0, item);
  movieState.scenes.forEach((s, i) => {
    s.scene_index = i + 1;
  });
  saveMovieStateCache();
  renderMovieScenes();
}

function clearMovieScenes() {
  if (movieState.scenes.length > 0) {
    if (!confirm('Bạn có chắc chắn muốn làm sạch toàn bộ kịch bản và phân cảnh để làm phim mới từ đầu không?')) return;
  }
  movieState.scenes = [];
  movieState.characters = [];
  movieState.sessionId = null;
  saveMovieStateCache();
  renderMovieCharacters();
  renderMovieScenes();
  renderCharacterLegend();
}

// ==================== @ CHARACTER MENTION AUTOCOMPLETE ====================
// User requirement: "gõ @ sẽ gợi ý những nhân vật có trong chuyện"
let activeMentionTarget = null;

function attachMentionListeners() {
  document.querySelectorAll('.mention-target').forEach(el => {
    setupMentionInput(el);
  });
}

function setupMentionInput(inputEl) {
  inputEl.addEventListener('input', (e) => handleMentionInput(e, inputEl));
  inputEl.addEventListener('keydown', (e) => handleMentionKeydown(e, inputEl));
}

function handleMentionInput(e, inputEl) {
  activeMentionTarget = inputEl;
  const cursor = inputEl.selectionStart;
  const textBefore = inputEl.value.substring(0, cursor);
  const atMatch = textBefore.match(/@([a-zA-Z0-9àáảãạăắằẳẵặâấầẩẫậđèéẻẽẹêếềểễệìíỉĩịòóỎõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵ\s]*)$/);

  const popup = document.getElementById('char-mention-autocomplete');
  if (!popup) return;

  if (atMatch && movieState.characters.length > 0) {
    const query = atMatch[1].toLowerCase().trim();
    const matches = movieState.characters.filter(c => c.name.toLowerCase().includes(query));

    if (matches.length > 0) {
      // Position popup below input
      const rect = inputEl.getBoundingClientRect();
      popup.style.top = `${rect.bottom + window.scrollY + 4}px`;
      popup.style.left = `${Math.min(rect.left + window.scrollX, window.innerWidth - 270)}px`;
      popup.classList.remove('hidden');

      popup.innerHTML = `
        <div class="px-2 py-1 text-[10px] font-bold text-slate-400 uppercase border-b border-white/[0.06]">
          Gợi ý nhân vật (@):
        </div>
        ${matches.map((char, mIdx) => `
          <button type="button" onclick="selectMentionCharacter('${escapeHtml(char.name)}')" class="mention-item w-full flex items-center space-x-2 px-2.5 py-1.5 rounded-lg hover:bg-surface-800 text-left transition ${mIdx === 0 ? 'bg-surface-800/60' : ''}">
            <span class="w-3 h-3 rounded-full shrink-0" style="background-color: ${char.color}"></span>
            <div class="min-w-0 flex-1">
              <span class="font-bold text-xs text-white block truncate">${escapeHtml(char.name)}</span>
              <span class="text-[10px] text-slate-400 block truncate">${escapeHtml(char.description || '')}</span>
            </div>
          </button>
        `).join('')}
      `;
      return;
    }
  }

  popup.classList.add('hidden');
}

function handleMentionKeydown(e, inputEl) {
  const popup = document.getElementById('char-mention-autocomplete');
  if (!popup || popup.classList.contains('hidden')) return;

  if (e.key === 'Escape') {
    popup.classList.add('hidden');
  } else if (e.key === 'Enter') {
    const firstBtn = popup.querySelector('.mention-item');
    if (firstBtn) {
      e.preventDefault();
      firstBtn.click();
    }
  }
}

function selectMentionCharacter(charName) {
  const popup = document.getElementById('char-mention-autocomplete');
  if (popup) popup.classList.add('hidden');

  if (!activeMentionTarget) return;

  const cursor = activeMentionTarget.selectionStart;
  const text = activeMentionTarget.value;
  const before = text.substring(0, cursor);
  const after = text.substring(cursor);

  const atIdx = before.lastIndexOf('@');
  if (atIdx !== -1) {
    activeMentionTarget.value = before.substring(0, atIdx) + `${charName} ` + after;
    activeMentionTarget.focus();
    activeMentionTarget.dispatchEvent(new Event('input'));
  }
}

// Close popup on outside click
document.addEventListener('click', (e) => {
  const popup = document.getElementById('char-mention-autocomplete');
  if (popup && !popup.contains(e.target) && e.target !== activeMentionTarget) {
    popup.classList.add('hidden');
  }
});

// ==================== AUTO MOVIE GENERATION SEQUENTIAL ENGINE ====================
// User requirement: "có nút cho phép tạo phim tự động, sẽ tạo tự động theo từng phân cảnh trong kịch bản theo hình thức nối tiếp (để giữ lại các đặc trưng nhân vật)"
let movieTimerInterval = null;

async function startAutoMovieGeneration() {
  if (movieState.scenes.length === 0) {
    alert('Chưa có phân cảnh nào. Vui lòng bấm [Phân Tích Kịch Bản] trước.');
    return;
  }

  if (!state.authenticated) {
    openAuthModal();
    return;
  }

  if (movieState.isGenerating) {
    alert('Hệ thống đang sản xuất phim...');
    return;
  }

  const unrenderedScenes = movieState.scenes.filter(s => !s.video_url);
  if (unrenderedScenes.length === 0) {
    const confirmRe = confirm('Tất cả phân cảnh đã hoàn thành video. Bạn có muốn quay lại từ đầu không?');
    if (!confirmRe) return;
    movieState.scenes.forEach(s => { s.video_url = null; s.status = 'idle'; });
    movieState.sessionId = null;
  }

  movieState.isGenerating = true;
  movieState.shouldStop = false;

  const banner = document.getElementById('movie-progress-banner');
  const bar = document.getElementById('movie-progress-bar');
  const titleEl = document.getElementById('movie-progress-title');
  const descEl = document.getElementById('movie-progress-desc');
  const timerEl = document.getElementById('movie-progress-timer');
  const btnStart = document.getElementById('btn-start-movie-gen');
  const btnStartText = document.getElementById('btn-start-movie-gen-text');

  if (banner) banner.classList.remove('hidden');
  if (btnStart) btnStart.disabled = true;
  if (btnStartText) btnStartText.innerText = 'Đang Sản Xuất Phim...';

  let totalSeconds = 0;
  if (movieTimerInterval) clearInterval(movieTimerInterval);
  movieTimerInterval = setInterval(() => {
    totalSeconds++;
    if (timerEl) timerEl.innerText = `⏱ ${totalSeconds}s`;
  }, 1000);

  const total = movieState.scenes.length;

  for (let i = 0; i < total; i++) {
    if (movieState.shouldStop) {
      alert('Đã dừng tiến trình sản xuất phim.');
      break;
    }

    const scene = movieState.scenes[i];
    if (scene.video_url) continue; // already generated

    movieState.currentGeneratingIndex = i;
    scene.status = 'generating';
    renderMovieScenes();

    if (titleEl) titleEl.innerText = `Đang sản xuất Cảnh ${i + 1}/${total}: "${escapeHtml(scene.situation).substring(0, 35)}..."`;
    if (descEl) descEl.innerText = `Áp dụng công nghệ nối tiếp giữ nhân vật · Khung hình ${movieState.aspectRatio} · ${scene.duration || 8}s`;
    if (bar) bar.style.width = `${Math.round(((i) / total) * 100)}%`;

    try {
      // Gather character reference images for this scene
      const involvedImages = [];
      (scene.characters_involved || []).forEach(name => {
        const char = movieState.characters.find(c => c.name.toLowerCase() === name.toLowerCase());
        if (char && char.images && char.images.length > 0) {
          char.images.forEach(img => {
            if (img.local_path) involvedImages.push(img.local_path);
          });
        }
      });

      // If no specific character image, collect any character images
      if (involvedImages.length === 0) {
        movieState.characters.forEach(c => {
          if (c.images) {
            c.images.forEach(img => {
              if (img.local_path) involvedImages.push(img.local_path);
            });
          }
        });
      }

      const isContinuation = (i > 0 && movieState.sessionId !== null);
      const payload = {
        prompt: scene.prompt || scene.situation,
        image_paths: involvedImages,
        aspect_ratio: movieState.aspectRatio,
        duration: parseInt(scene.duration, 10) || 8,
        is_continuation: isContinuation,
        session_id: movieState.sessionId,
        scene_index: scene.scene_index,
        style_preset: movieState.stylePreset,
        timeout: 600.0,
      };

      const res = await fetch('/api/video/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Không thể tạo phân cảnh.');

      if (data.session_id) {
        movieState.sessionId = data.session_id;
      }

      if (data.videos && data.videos.length > 0) {
        scene.video_url = data.videos[0];
        scene.status = 'done';
        saveMovieStateCache();
        renderMovieScenes();
        checkAuthStatus(true).catch(() => {});
      } else {
        throw new Error('Không nhận được video phân cảnh.');
      }
    } catch (err) {
      console.error(`Lỗi tạo cảnh #${scene.scene_index}:`, err);
      scene.status = 'error';
      renderMovieScenes();
      const cont = confirm(`Lỗi sản xuất Cảnh #${scene.scene_index}: ${err.message}
Bạn có muốn tiếp tục các cảnh còn lại không?`);
      if (!cont) break;
    }
  }

  // Finish
  clearInterval(movieTimerInterval);
  movieState.isGenerating = false;
  if (btnStart) btnStart.disabled = false;
  if (btnStartText) btnStartText.innerText = 'Bắt Đầu Tạo Phim Tự Động';
  if (bar) bar.style.width = '100%';

  setTimeout(() => {
    if (banner) banner.classList.add('hidden');
  }, 2500);

  const completedCount = movieState.scenes.filter(s => s.video_url).length;
  if (completedCount > 0) {
    const playNow = confirm(`🎉 Chúc mừng! Đã hoàn thành ${completedCount}/${total} phân cảnh phim!
Bạn có muốn mở Rạp Chiếu Phim để xem liên tục toàn bộ bộ phim ngay không?`);
    if (playNow) {
      openCinemaTheater();
    }
  }
}

function stopAutoMovieGeneration() {
  movieState.shouldStop = true;
}

// Generate single scene manually
async function generateSingleMovieScene(idx) {
  const scene = movieState.scenes[idx];
  if (!scene) return;

  if (!state.authenticated) {
    openAuthModal();
    return;
  }

  scene.status = 'generating';
  renderMovieScenes();

  try {
    const involvedImages = [];
    (scene.characters_involved || []).forEach(name => {
      const char = movieState.characters.find(c => c.name.toLowerCase() === name.toLowerCase());
      if (char && char.images) {
        char.images.forEach(img => {
          if (img.local_path) involvedImages.push(img.local_path);
        });
      }
    });

    const isContinuation = (idx > 0 && movieState.sessionId !== null);
    const payload = {
      prompt: scene.prompt || scene.situation,
      image_paths: involvedImages,
      aspect_ratio: movieState.aspectRatio,
      duration: parseInt(scene.duration, 10) || 8,
      is_continuation: isContinuation,
      session_id: movieState.sessionId,
      scene_index: scene.scene_index,
      style_preset: movieState.stylePreset,
      timeout: 600.0,
    };

    const res = await fetch('/api/video/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Không thể tạo phân cảnh.');

    if (data.session_id) movieState.sessionId = data.session_id;

    if (data.videos && data.videos.length > 0) {
      scene.video_url = data.videos[0];
      scene.status = 'done';
      saveMovieStateCache();
      renderMovieScenes();
      checkAuthStatus(true).catch(() => {});
    }
  } catch (err) {
    alert(`Lỗi quay cảnh #${scene.scene_index}: ` + err.message);
    scene.status = 'idle';
    renderMovieScenes();
  }
}

// ==================== CINEMA THEATER CONTINUOUS PLAYBACK ====================
// User requirement: "có 1 nút bấm để phát phim, sẽ phát từng phân cảnh, tự chiếu qua phân cảnh khác khi hết phân cảnh trước đó"
function openCinemaTheater() {
  const readyScenes = movieState.scenes.filter(s => s.video_url);
  if (readyScenes.length === 0) {
    alert('Chưa có phân cảnh nào được xuất bản thành video.\\nHãy bấm [Bắt Đầu Tạo Phim Tự Động] để sản xuất các phân cảnh trước!');
    return;
  }

  const modal = document.getElementById('movie-theater-modal');
  if (!modal) return;

  modal.classList.remove('hidden');
  document.getElementById('theater-movie-title').innerText = movieState.title || 'H-AI Cinema Masterpiece';

  theaterPlayScene(0);
}

function closeCinemaTheater() {
  const modal = document.getElementById('movie-theater-modal');
  const player = document.getElementById('theater-video-player');
  if (player) {
    player.pause();
    player.src = '';
  }
  if (modal) modal.classList.add('hidden');
}

function theaterPlayScene(readyIndex) {
  const readyScenes = movieState.scenes.filter(s => s.video_url);
  if (!readyScenes[readyIndex]) return;

  movieState.theaterCurrentIndex = readyIndex;
  const currentScene = readyScenes[readyIndex];

  const player = document.getElementById('theater-video-player');
  const indicator = document.getElementById('theater-scene-indicator');
  const dialogueEl = document.getElementById('theater-subtitle-dialogue');
  const situationEl = document.getElementById('theater-subtitle-situation');

  if (indicator) {
    indicator.innerText = `Đang chiếu Cảnh #${currentScene.scene_index} (Phân cảnh ${readyIndex + 1}/${readyScenes.length})`;
  }
  if (dialogueEl) {
    dialogueEl.innerText = currentScene.dialogue ? `"${currentScene.dialogue}"` : '';
  }
  if (situationEl) {
    situationEl.innerText = currentScene.situation || '';
  }

  if (player) {
    player.src = currentScene.video_url;
    player.play().catch(() => {});

    // Seamless automatic transition to next scene when current scene ends
    player.onended = () => {
      theaterNextScene();
    };
  }

  renderTheaterPlaylistStrip();
  lucide.createIcons();
}

function theaterNextScene() {
  const readyScenes = movieState.scenes.filter(s => s.video_url);
  if (movieState.theaterCurrentIndex < readyScenes.length - 1) {
    theaterPlayScene(movieState.theaterCurrentIndex + 1);
  } else {
    // End of movie
    const playAgain = confirm('🎬 Đã chiếu hết toàn bộ các phân cảnh trong bộ phim!\\nBạn có muốn phát lại từ đầu không?');
    if (playAgain) {
      theaterPlayScene(0);
    }
  }
}

function theaterPrevScene() {
  if (movieState.theaterCurrentIndex > 0) {
    theaterPlayScene(movieState.theaterCurrentIndex - 1);
  }
}

function theaterTogglePlay() {
  const player = document.getElementById('theater-video-player');
  const icon = document.getElementById('theater-play-icon');
  const text = document.getElementById('theater-play-text');
  if (!player) return;

  if (player.paused) {
    player.play();
    if (text) text.innerText = 'Tạm dừng';
    if (icon) icon.setAttribute('data-lucide', 'pause');
  } else {
    player.pause();
    if (text) text.innerText = 'Phát';
    if (icon) icon.setAttribute('data-lucide', 'play');
  }
  lucide.createIcons();
}

function renderTheaterPlaylistStrip() {
  const strip = document.getElementById('theater-playlist-strip');
  if (!strip) return;

  const readyScenes = movieState.scenes.filter(s => s.video_url);
  strip.innerHTML = '';

  readyScenes.forEach((sc, idx) => {
    const isPlaying = idx === movieState.theaterCurrentIndex;
    const item = document.createElement('div');
    item.className = `shrink-0 w-36 p-2 rounded-xl border transition cursor-pointer flex items-center space-x-2 select-none ${
      isPlaying
        ? 'border-cyan-400 bg-cyan-950/60 ring-2 ring-cyan-400/40'
        : 'border-white/10 bg-surface-950/60 hover:bg-surface-800'
    }`;
    item.onclick = () => theaterPlayScene(idx);

    item.innerHTML = `
      <div class="w-7 h-7 rounded-lg ${isPlaying ? 'bg-cyan-500 text-white animate-pulse' : 'bg-surface-800 text-slate-400'} flex items-center justify-center shrink-0">
        <i data-lucide="${isPlaying ? 'play' : 'film'}" class="w-3.5 h-3.5 fill-current"></i>
      </div>
      <div class="min-w-0">
        <span class="text-[11px] font-bold ${isPlaying ? 'text-cyan-300' : 'text-slate-200'} block truncate">Cảnh #${sc.scene_index}</span>
        <span class="text-[9px] text-slate-400 block truncate">${sc.duration || 8}s · ${escapeHtml(sc.situation || '')}</span>
      </div>
    `;
    strip.appendChild(item);
  });
  lucide.createIcons();
}

// ==================== MERGE & DOWNLOAD MOVIE ====================
// User requirement: "merger các phân cảnh thành video và tải xuống"
async function mergeAndDownloadMovie() {
  const readyScenes = movieState.scenes.filter(s => s.video_url);
  if (readyScenes.length === 0) {
    alert('Không có phân cảnh nào đã hoàn thành video để ghép.');
    return;
  }

  const btn = document.getElementById('btn-merge-movie');
  const originalText = btn ? btn.innerHTML : 'Ghép & Tải MP4';

  if (readyScenes.length === 1) {
    triggerDownload(readyScenes[0].video_url, `H-AI_Movie_Scene1_${Date.now()}.mp4`);
    return;
  }

  try {
    if (btn) {
      btn.disabled = true;
      btn.innerText = `Đang ghép ${readyScenes.length} cảnh...`;
    }

    const res = await fetch('/api/video/merge', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        video_urls: readyScenes.map(s => s.video_url),
        output_filename: `H-AI_FullMovie_${Date.now()}.mp4`,
      }),
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Không thể ghép video.');

    triggerDownload(data.url, data.filename || `H-AI_FullMovie_${Date.now()}.mp4`);
    alert(`🎬 Ghép thành công ${readyScenes.length} phân cảnh thành 1 bộ phim hoàn chỉnh!
Trình duyệt đang tự động tải file MP4 về máy.`);
  } catch (err) {
    alert('Lỗi ghép phim: ' + err.message);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = originalText;
      lucide.createIcons();
    }
  }
}
