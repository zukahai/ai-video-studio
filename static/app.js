// State Management
const state = {
  currentTab: 'video',
  authenticated: false,
  email: null,
  videoImageFile: null,
  imageFile: null,
  chatSessionId: null,
  aspectRatio: '1:1',
};

// Initialize Application
document.addEventListener('DOMContentLoaded', () => {
  checkAuthStatus();
  setupDropzones();
});

// Tab Navigation
function switchTab(tabId) {
  state.currentTab = tabId;
  document.querySelectorAll('.tab-view').forEach(el => el.classList.add('hidden'));
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.classList.remove('text-white', 'bg-blue-600', 'shadow-md', 'shadow-blue-600/30');
    btn.classList.add('text-slate-400', 'hover:text-white', 'hover:bg-surface-700/50');
  });

  const activeView = document.getElementById(`view-${tabId}`);
  const activeBtn = document.getElementById(`tab-btn-${tabId}`);
  if (activeView) activeView.classList.remove('hidden');
  if (activeBtn) {
    activeBtn.classList.remove('text-slate-400', 'hover:text-white', 'hover:bg-surface-700/50');
    activeBtn.classList.add('text-white', 'bg-blue-600', 'shadow-md', 'shadow-blue-600/30');
  }

  if (tabId === 'gallery') {
    loadGallery();
  }
  lucide.createIcons();
}

// Authentication Flow
async function checkAuthStatus() {
  try {
    const res = await fetch('/api/auth/status');
    const data = await res.json();
    const badge = document.getElementById('status-badge');
    const text = document.getElementById('status-text');
    const btnText = document.getElementById('auth-btn-text');

    state.authenticated = data.authenticated && data.is_alive;
    state.email = data.email;

    if (state.authenticated) {
      badge.className = 'flex items-center space-x-2 px-3 py-1.5 rounded-full text-xs font-medium border bg-green-500/10 text-green-300 border-green-500/20';
      badge.innerHTML = `<span class="w-2 h-2 rounded-full bg-green-400"></span><span>Đã kết nối: ${data.email || 'Muse AI'}</span>`;
      btnText.innerText = 'Đổi tài khoản';
    } else {
      badge.className = 'flex items-center space-x-2 px-3 py-1.5 rounded-full text-xs font-medium border bg-amber-500/10 text-amber-300 border-amber-500/20';
      badge.innerHTML = `<span class="w-2 h-2 rounded-full bg-amber-400 animate-pulse"></span><span>Chưa kết nối Muse OTP</span>`;
      btnText.innerText = 'Đăng nhập OTP';
    }
  } catch (err) {
    console.error('Lỗi kiểm tra auth:', err);
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
    isError ? 'bg-red-500/10 border border-red-500/20 text-red-300' : 'bg-green-500/10 border border-green-500/20 text-green-300'
  }`;
  msg.classList.remove('hidden');
}

function hideModalMsg() {
  document.getElementById('modal-msg').classList.add('hidden');
}

async function requestOtp() {
  const emailInput = document.getElementById('auth-email-input');
  const email = emailInput.value.trim();
  if (!email || !email.includes('@')) {
    showModalMsg('Vui lòng nhập địa chỉ email hợp lệ.');
    return;
  }

  const btn = document.getElementById('btn-request-otp');
  const originalHtml = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<span class="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></span><span>Đang gửi mã...</span>`;
  hideModalMsg();

  try {
    const res = await fetch('/api/auth/request-otp', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email, region: 'US' }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || 'Không thể gửi mã OTP.');
    }

    document.getElementById('otp-step-1').classList.add('hidden');
    document.getElementById('otp-step-2').classList.remove('hidden');
    showModalMsg(`Đã gửi mã OTP đến ${email}! Hãy nhập mã 6 số bên dưới.`, false);
  } catch (err) {
    showModalMsg(err.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalHtml;
  }
}

function backToStep1() {
  document.getElementById('otp-step-1').classList.remove('hidden');
  document.getElementById('otp-step-2').classList.add('hidden');
  hideModalMsg();
}

async function verifyOtp() {
  const otpInput = document.getElementById('auth-otp-input');
  const code = otpInput.value.trim();
  if (!code || code.length < 4) {
    showModalMsg('Vui lòng nhập đầy đủ mã OTP.');
    return;
  }

  const btn = document.getElementById('btn-verify-otp');
  const originalHtml = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<span class="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"></span><span>Đang xác nhận...</span>`;
  hideModalMsg();

  try {
    const res = await fetch('/api/auth/verify-otp', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ otp_code: code }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || 'Mã OTP không đúng hoặc đã hết hạn.');
    }

    showModalMsg('Đăng nhập thành công! Đang lưu phiên...', false);
    setTimeout(() => {
      closeAuthModal();
      checkAuthStatus();
    }, 1200);
  } catch (err) {
    showModalMsg(err.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalHtml;
  }
}

// Upload & Reference Image Logic
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
      dropzone.classList.add('border-blue-500');
    });

    dropzone.addEventListener('dragleave', () => {
      dropzone.classList.remove('border-blue-500');
    });

    dropzone.addEventListener('drop', (e) => {
      e.preventDefault();
      dropzone.classList.remove('border-blue-500');
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

// Preset Prompts
function setVideoPrompt(text) {
  document.getElementById('video-prompt').value = text;
}

// Aspect Ratio Selector
function setAspectRatio(ratio) {
  state.aspectRatio = ratio;
  ['1:1', '16:9', '9:16'].forEach(r => {
    const btn = document.getElementById(`ratio-${r.replace(':', '-')}`);
    if (r === ratio) {
      btn.className = 'ratio-btn border border-indigo-500 bg-indigo-500/20 text-white rounded-xl py-2 px-3 text-xs font-semibold flex flex-col items-center space-y-1';
    } else {
      btn.className = 'ratio-btn border border-surface-700 bg-surface-900 text-slate-400 hover:text-white rounded-xl py-2 px-3 text-xs font-semibold flex flex-col items-center space-y-1';
    }
  });
}

// VIDEO GENERATION
let videoTimerInterval = null;

async function generateVideo() {
  const prompt = document.getElementById('video-prompt').value.trim();
  if (!prompt) {
    alert('Vui lòng nhập nội dung mô tả video.');
    return;
  }

  if (!state.authenticated) {
    openAuthModal();
    return;
  }

  const btn = document.getElementById('btn-generate-video');
  btn.disabled = true;

  // UI state transitions
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
      document.getElementById('video-progress-text').innerText = 'Hatch VM đã sẵn sàng. Đang tiến hành render video...';
    } else if (seconds === 60) {
      document.getElementById('video-progress-text').innerText = 'Đang xử lý chuyển động và tổng hợp âm thanh native audio...';
    } else if (seconds === 120) {
      document.getElementById('video-progress-text').innerText = 'Đang hoàn tất và đóng gói file MP4 chất lượng cao...';
    }
  }, 1000);

  try {
    const payload = {
      prompt: prompt,
      image_paths: state.videoImageFile ? [state.videoImageFile] : [],
      timeout: 600.0,
    };

    const res = await fetch('/api/video/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Không thể tạo video từ Muse.');

    if (data.videos && data.videos.length > 0) {
      const videoUrl = data.videos[0];
      const player = document.getElementById('video-player');
      player.src = videoUrl;
      document.getElementById('video-download-btn').href = videoUrl;

      document.getElementById('video-loading-state').classList.add('hidden');
      document.getElementById('video-result-container').classList.remove('hidden');
      player.play().catch(() => {});
    } else {
      throw new Error('Muse không trả về video nào trong phiên làm việc.');
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

// IMAGE GENERATION
async function generateImage() {
  const prompt = document.getElementById('image-prompt').value.trim();
  if (!prompt) {
    alert('Vui lòng nhập mô tả ảnh.');
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
    if (!res.ok) throw new Error(data.detail || 'Không thể tạo ảnh từ Muse.');

    if (data.images && data.images.length > 0) {
      const imgUrl = data.images[0];
      document.getElementById('image-result').src = imgUrl;
      document.getElementById('image-download-btn').href = imgUrl;

      document.getElementById('image-loading-state').classList.add('hidden');
      document.getElementById('image-result-container').classList.remove('hidden');
    } else {
      throw new Error('Không nhận được ảnh trả về từ Muse.');
    }
  } catch (err) {
    alert('Lỗi tạo ảnh: ' + err.message);
    document.getElementById('image-loading-state').classList.add('hidden');
    document.getElementById('image-idle-state').classList.remove('hidden');
  } finally {
    btn.disabled = false;
  }
}

// CHAT ASSISTANT
async function sendChatMessage(e) {
  e.preventDefault();
  const input = document.getElementById('chat-input');
  const text = input.value.trim();
  if (!text) return;

  const container = document.getElementById('chat-messages');

  // Append user message
  const userBubble = document.createElement('div');
  userBubble.className = 'flex items-start justify-end space-x-3';
  userBubble.innerHTML = `
    <div class="bg-blue-600 rounded-2xl rounded-tr-none p-3.5 max-w-xl text-sm text-white shadow">
      ${escapeHtml(text)}
    </div>
    <div class="w-7 h-7 rounded-lg bg-surface-700 text-slate-300 flex items-center justify-center shrink-0 border border-surface-600">
      <i data-lucide="user" class="w-4 h-4"></i>
    </div>
  `;
  container.appendChild(userBubble);
  input.value = '';
  container.scrollTop = container.scrollHeight;
  lucide.createIcons();

  // Assistant typing bubble
  const typingBubble = document.createElement('div');
  typingBubble.className = 'flex items-start space-x-3';
  typingBubble.id = 'chat-typing';
  typingBubble.innerHTML = `
    <div class="w-7 h-7 rounded-lg bg-blue-600/20 text-blue-400 flex items-center justify-center shrink-0 border border-blue-500/30">
      <i data-lucide="bot" class="w-4 h-4"></i>
    </div>
    <div class="bg-surface-700/70 border border-surface-600/50 rounded-2xl rounded-tl-none p-3.5 max-w-xl text-sm text-slate-400 flex items-center space-x-2">
      <span class="w-2 h-2 rounded-full bg-blue-400 animate-ping"></span>
      <span>Muse Spark đang suy nghĩ...</span>
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

    if (!res.ok) throw new Error(data.detail || 'Lỗi gửi tin nhắn.');

    if (data.session_id) state.chatSessionId = data.session_id;

    const botBubble = document.createElement('div');
    botBubble.className = 'flex items-start space-x-3';
    botBubble.innerHTML = `
      <div class="w-7 h-7 rounded-lg bg-blue-600/20 text-blue-400 flex items-center justify-center shrink-0 border border-blue-500/30">
        <i data-lucide="bot" class="w-4 h-4"></i>
      </div>
      <div class="bg-surface-700/70 border border-surface-600/50 rounded-2xl rounded-tl-none p-3.5 max-w-xl text-sm text-slate-200 space-y-2">
        <p class="whitespace-pre-wrap">${escapeHtml(data.reply)}</p>
        <button onclick="useAsVideoPrompt(${JSON.stringify(data.reply)})" class="mt-2 text-[11px] text-blue-400 hover:text-blue-300 font-medium flex items-center space-x-1 border border-blue-500/30 bg-blue-500/10 px-2 py-1 rounded-md transition">
          <i data-lucide="clapperboard" class="w-3 h-3"></i>
          <span>Dùng nội dung này làm prompt Video</span>
        </button>
      </div>
    `;
    container.appendChild(botBubble);
    container.scrollTop = container.scrollHeight;
    lucide.createIcons();
  } catch (err) {
    typingBubble.remove();
    const errorBubble = document.createElement('div');
    errorBubble.className = 'text-xs text-red-400 p-2 text-center';
    errorBubble.innerText = 'Lỗi chat: ' + err.message;
    container.appendChild(errorBubble);
  }
}

function clearChat() {
  state.chatSessionId = null;
  const container = document.getElementById('chat-messages');
  container.innerHTML = `
    <div class="flex items-start space-x-3">
      <div class="w-7 h-7 rounded-lg bg-blue-600/20 text-blue-400 flex items-center justify-center shrink-0 border border-blue-500/30">
        <i data-lucide="bot" class="w-4 h-4"></i>
      </div>
      <div class="bg-surface-700/70 border border-surface-600/50 rounded-2xl rounded-tl-none p-3.5 max-w-xl text-sm text-slate-200">
        Đoạn hội thoại đã được đặt lại. Bạn muốn Muse giúp gì tiếp theo?
      </div>
    </div>
  `;
  lucide.createIcons();
}

function useAsVideoPrompt(text) {
  switchTab('video');
  document.getElementById('video-prompt').value = text;
}

// GALLERY
async function loadGallery() {
  const grid = document.getElementById('gallery-grid');
  grid.innerHTML = `<div class="col-span-full py-12 text-center text-slate-500">Đang tải danh sách file...</div>`;

  try {
    const res = await fetch('/api/gallery');
    const data = await res.json();

    if (!data.items || data.items.length === 0) {
      grid.innerHTML = `
        <div class="col-span-full py-16 text-center text-slate-500 space-y-2">
          <i data-lucide="film" class="w-10 h-10 mx-auto text-slate-600"></i>
          <p class="text-sm">Chưa có video hoặc ảnh nào được tạo.</p>
        </div>
      `;
      lucide.createIcons();
      return;
    }

    grid.innerHTML = '';
    data.items.forEach(item => {
      const card = document.createElement('div');
      card.className = 'bg-surface-800 border border-surface-700 rounded-xl overflow-hidden shadow-lg group hover:border-surface-600 transition flex flex-col justify-between';

      const mediaHtml = item.type === 'video'
        ? `<div class="aspect-video bg-black flex items-center justify-center relative">
             <video src="${item.url}" class="w-full h-full object-cover" controls playsinline></video>
           </div>`
        : `<div class="aspect-square bg-black/40 flex items-center justify-center overflow-hidden">
             <img src="${item.url}" class="w-full h-full object-cover group-hover:scale-105 transition duration-300">
           </div>`;

      card.innerHTML = `
        ${mediaHtml}
        <div class="p-3 space-y-2">
          <div class="flex items-center justify-between text-xs">
            <span class="font-medium text-slate-200 truncate max-w-[140px]">${item.name}</span>
            <span class="text-slate-400 text-[10px]">${item.size_mb} MB</span>
          </div>
          <div class="flex items-center justify-between pt-1">
            <span class="text-[10px] uppercase font-bold tracking-wider px-1.5 py-0.5 rounded ${item.type === 'video' ? 'bg-blue-500/20 text-blue-400' : 'bg-indigo-500/20 text-indigo-400'}">
              ${item.type}
            </span>
            <a href="${item.url}" download class="p-1.5 rounded-lg bg-surface-700 hover:bg-surface-600 text-slate-200 transition" title="Tải về">
              <i data-lucide="download" class="w-3.5 h-3.5"></i>
            </a>
          </div>
        </div>
      `;
      grid.appendChild(card);
    });
    lucide.createIcons();
  } catch (err) {
    grid.innerHTML = `<div class="col-span-full py-12 text-center text-red-400 text-sm">Lỗi tải bộ sưu tập: ${err.message}</div>`;
  }
}

// Helper: Escape HTML
function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
