import sys
import os
import platform
import ctypes

# === BỘ CHỐNG CRASH NGẦM CHO MAC (FIX TRIỆT ĐỂ LỖI NỀN ĐEN XÌ) ===
if getattr(sys, 'frozen', False):
    class NullWriter:
        def write(self, *args, **kwargs): pass
        def flush(self, *args, **kwargs): pass
    sys.stdout = NullWriter()
    sys.stderr = NullWriter()
# ==============================================================

import subprocess
import glob
import threading
import time
import re
import io
import math
import concurrent.futures

def get_resource_path(relative_path):
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)

def get_app_support_path():
    """Tự động nhận diện Hệ điều hành để tạo thư mục lưu Token chuẩn"""
    if platform.system() == "Windows":
        # Chuẩn của Windows: C:\Users\Username\AppData\Local\Auto Drive DESI
        path = os.path.join(os.getenv('LOCALAPPDATA'), 'Auto Drive DESI')
    else:
        # Chuẩn của macOS
        path = os.path.join(os.path.expanduser('~'), 'Library', 'Application Support', 'Auto Drive DESI')
    
    os.makedirs(path, exist_ok=True)
    return path

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload

# Cấu hình UI
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

SCOPES = ['https://www.googleapis.com/auth/drive']

class AutoDriveDESI(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Auto Drive DESI ( LIFETIME)")
        self.geometry("750x950")
        self.selected_path = ""
        self.drive_service = None
        self.sync_active = False
        self.download_sync_active = False
       
        self.is_paused = False
        self.is_cancelled = False
        self.lock = threading.Lock()
       
        self.folder_cache = {}
        self.folder_lock = threading.Lock()
        self.successfully_uploaded = set()
        self.downloaded_files_log = set()
       
        self.total_bytes = 0
        self.uploaded_bytes = 0
        self.start_time = 0
        self.total_files = 0
        self.completed_files = 0
        
        # === Biến quản lý chống ngủ ===
        self.caffeinate_process = None
       
        self.setup_ui()
        self.check_auth_status()
        
        # Chỉ kích hoạt mẹo giật màn hình cho Mac, bỏ qua nếu là Windows
        if platform.system() == "Darwin":
            self.after(200, self.force_render)

    def force_render(self):
        """Giật nhẹ kích thước cửa sổ để ép Mac M-chip vẽ giao diện"""
        self.geometry("751x950")
        self.update_idletasks()
        self.geometry("750x950")

    def setup_ui(self):
        self.header = ctk.CTkLabel(self, text="Auto Drive DESI", font=("Arial", 24, "bold"), text_color="#3B8ED0")
        self.header.pack(pady=(20, 10))

        self.account_frame = ctk.CTkFrame(self)
        self.account_frame.pack(pady=10, padx=20, fill="x")
        self.lbl_account = ctk.CTkLabel(self.account_frame, text="Trạng thái: Chưa kết nối", font=("Arial", 12))
        self.lbl_account.pack(side="left", padx=10)
        self.btn_auth = ctk.CTkButton(self.account_frame, text="Đăng nhập", width=120, command=self.authenticate_drive)
        self.btn_auth.pack(side="right", padx=10)

        self.btn_select = ctk.CTkButton(self, text="📁 Chọn thư mục dự án", command=self.select_folder)
        self.btn_select.pack(pady=10)
        self.lbl_path = ctk.CTkLabel(self, text="Chưa chọn folder...", text_color="gray")
        self.lbl_path.pack()

        self.lbl_full_path = ctk.CTkLabel(self, text="", text_color="#888888", font=("Arial", 10))
        self.lbl_full_path.pack(pady=(0, 5))

        self.lbl_drive_info = ctk.CTkLabel(self, text="🔗 Drive đích: (sẽ cập nhật khi bắt đầu xử lý)", 
                                           text_color="#888888", font=("Arial", 10))
        self.lbl_drive_info.pack(pady=(0, 10))

        self.entry_drive_id = ctk.CTkEntry(self, width=500, placeholder_text="ID hoặc Link Thư mục đích trên DRIVE (Để trống = My Drive)")
        self.entry_drive_id.pack(pady=5)

        self.mode_var = tk.StringVar(value="sync")
        self.mode_var.trace_add("write", self.update_button_label)
       
        ctk.CTkRadioButton(self, text="Upload Nguyên bản (Maxping 8 Luồng - Thẳng Drive)", variable=self.mode_var, value="raw_up").pack(pady=5, anchor="w", padx=40)
        ctk.CTkRadioButton(self, text="Nén 7Z & Upload Thẳng", variable=self.mode_var, value="zip_up").pack(pady=5, anchor="w", padx=40)
        ctk.CTkRadioButton(self, text="Chỉ Nén 7Z (Lưu trữ ổ cứng)", variable=self.mode_var, value="zip_only").pack(pady=5, anchor="w", padx=40)
        ctk.CTkRadioButton(self, text="Tải từ Drive về máy (Maxping 8 Luồng & Tự quét file mới)", variable=self.mode_var, value="download").pack(pady=5, anchor="w", padx=40)
        ctk.CTkRadioButton(self, text="🔁 Auto Sync (Quét toàn bộ & Tự đẩy file mới)", variable=self.mode_var, value="sync").pack(pady=5, anchor="w", padx=40)
       
        self.opt_split = ctk.CTkOptionMenu(self, values=["Không chia", "Chia 2 phần", "Chia 3 phần", "Chia 4 phần"])
        self.opt_split.set("Không chia")
        self.opt_split.pack(pady=10)

        self.progress_bar = ctk.CTkProgressBar(self)
        self.progress_bar.pack(pady=20, fill="x", padx=40)
        self.progress_bar.set(0)

        self.lbl_info = ctk.CTkLabel(self, text="Tiến độ: Sẵn sàng...", font=("Arial", 12))
        self.lbl_info.pack()
        self.lbl_speed = ctk.CTkLabel(self, text="0 MB/s | ETA: --:-- | File: 0/0", font=("Consolas", 12))
        self.lbl_speed.pack()

        self.link_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.link_frame.pack(pady=10, fill="x", padx=40)
       
        self.entry_link = ctk.CTkEntry(self.link_frame, placeholder_text="Link Thư mục đích (Public) sẽ hiện ở đây...")
        self.entry_link.pack(side="left", fill="x", expand=True, padx=(0, 10))
       
        self.btn_copy = ctk.CTkButton(self.link_frame, text="📋 Copy Link", width=100, fg_color="#8E44AD", command=self.copy_link)
        self.btn_copy.pack(side="right")

        self.btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.btn_frame.pack(pady=10)
       
        self.btn_start = ctk.CTkButton(self.btn_frame, text="🚀 BẮT ĐẦU SYNC", height=45, fg_color="blue", command=self.run_process)
        self.btn_start.pack(side="left", padx=5)
       
        self.btn_pause = ctk.CTkButton(self.btn_frame, text="⏸️ TẠM DỪNG", height=45, fg_color="#F39C12", state="disabled", command=self.toggle_pause)
        self.btn_pause.pack(side="left", padx=5)
       
        self.btn_cancel = ctk.CTkButton(self.btn_frame, text="⏹️ HỦY BỎ", height=45, fg_color="#C0392B", state="disabled", command=self.cancel_process)
        self.btn_cancel.pack(side="left", padx=5)

        self.copyright_label = ctk.CTkLabel(self, text="@copyright by desimedia", 
                                            font=("Arial", 9, "italic"), text_color="#555555")
        self.copyright_label.pack(pady=(25, 10))

    def copy_link(self):
        link_text = self.entry_link.get()
        if link_text:
            self.clipboard_clear()
            self.clipboard_append(link_text)
            self.update()
            messagebox.showinfo("Đã sao chép", "Đã copy link Public vào bộ nhớ tạm! Sẵn sàng gửi khách.", parent=self)
        else:
            messagebox.showwarning("Trống", "Chưa có link để copy!", parent=self)

    def update_button_label(self, *args):
        if self.sync_active or self.download_sync_active:
            messagebox.showwarning("Cảnh báo", "Vui lòng dừng tiến trình tự động trước khi đổi tính năng!", parent=self)
            return
        mode = self.mode_var.get()
        if mode == "download":
            self.btn_select.configure(text="💾 CHỌN NƠI LƯU VỀ")
            self.btn_start.configure(text="🚀 BẮT ĐẦU AUTO DOWNLOAD", fg_color="green")
        elif mode == "sync":
            self.btn_select.configure(text="🔁 CHỌN FOLDER ĐỂ SYNC")
            self.btn_start.configure(text="🚀 BẮT ĐẦU SYNC", fg_color="blue")
        else:
            self.btn_select.configure(text="📁 CHỌN THƯ MỤC DỰ ÁN")
            self.btn_start.configure(text="🚀 XỬ LÝ DỰ ÁN", fg_color="green")

    def select_folder(self):
        folder = ctk.filedialog.askdirectory()
        if folder:
            self.selected_path = os.path.normpath(folder)
            base_name = os.path.basename(self.selected_path)
            self.lbl_path.configure(text=f"📁 {base_name}")
            self.lbl_full_path.configure(text=f"📂 Đường dẫn đầy đủ: {self.selected_path}")

    def extract_id_from_link(self, text):
        match = re.search(r'/(?:folders|file/d)/([a-zA-Z0-9_-]+)', text)
        return match.group(1) if match else text

    def check_auth_status(self):
        token_path = os.path.join(get_app_support_path(), 'token.json')
        if os.path.exists(token_path):
            try:
                self.creds = Credentials.from_authorized_user_file(token_path, SCOPES)
                if self.creds and self.creds.expired and self.creds.refresh_token:
                    self.creds.refresh(Request())
                    with open(token_path, 'w') as token:
                        token.write(self.creds.to_json())
               
                if self.creds and self.creds.valid:
                    self.drive_service = build('drive', 'v3', credentials=self.creds)
                    self.lbl_account.configure(text="Trạng thái: Đã kết nối", text_color="#2ECC71")
                    self.btn_auth.configure(text="Đổi TK", fg_color="#E74C3C")
                    self.update_idletasks()
                    return
            except Exception as e:
                if os.path.exists(token_path): os.remove(token_path)
        self.lbl_account.configure(text="Trạng thái: Chưa kết nối", text_color="white")
        self.btn_auth.configure(text="Đăng nhập", fg_color="#3B8ED0")

    def authenticate_drive(self):
        token_path = os.path.join(get_app_support_path(), 'token.json')
        if os.path.exists(token_path): os.remove(token_path)
        cred_path = get_resource_path('credentials.json')
        flow = InstalledAppFlow.from_client_secrets_file(cred_path, SCOPES)
        self.creds = flow.run_local_server(port=0)
        with open(token_path, 'w') as token: token.write(self.creds.to_json())
        self.check_auth_status()

    def update_ui(self, percent, info, speed_text=""):
        self.after(0, self._safe_update_ui, percent, info, speed_text)
       
    def _safe_update_ui(self, percent, info, speed_text):
        self.progress_bar.set(percent / 100)
        self.lbl_info.configure(text=info)
        if speed_text: self.lbl_speed.configure(text=speed_text)

    def toggle_pause(self):
        self.is_paused = not self.is_paused
        if self.is_paused:
            self.btn_pause.configure(text="▶️ TIẾP TỤC", fg_color="#27AE60")
            self.update_ui(self.progress_bar.get() * 100, "⏸️ ĐÃ TẠM DỪNG - Nhấn 'Tiếp tục' để chạy tiếp")
            self.lbl_speed.configure(text="⏸️ TẠM DỪNG | File: {}/{}".format(
                getattr(self, 'completed_files', 0), getattr(self, 'total_files', 0)))
        else:
            self.btn_pause.configure(text="⏸️ TẠM DỪNG", fg_color="#F39C12")
            self.start_time = time.time()
            self.update_ui(self.progress_bar.get() * 100, "▶️ Tiếp tục xử lý...")

    def cancel_process(self):
        response = messagebox.askyesno("Xác nhận", "Hủy/Dừng hoàn toàn tiến trình đang chạy?", parent=self)
        if response:
            self.is_cancelled = True
            self.is_paused = False
            self.sync_active = False
            self.download_sync_active = False
            self.btn_pause.configure(state="disabled")
            self.btn_cancel.configure(state="disabled")
           
            # === RESET TOÀN BỘ TRẠNG THÁI ===
            self.start_time = 0
            self.uploaded_bytes = 0
            self.total_bytes = 0
            self.total_files = 0
            self.completed_files = 0
            self.successfully_uploaded.clear()
           
            self.update_button_label()
            self.btn_start.configure(state="normal")
            self.update_ui(0, "❌ Tiến trình đã dừng hoàn toàn.", "0 MB/s | ETA: --:-- | File: 0/0")
            self.lbl_speed.configure(text="0 MB/s | ETA: --:-- | File: 0/0")
            self.stop_keep_awake()

    # === TÍNH NĂNG GIỮ MÁY TÍNH KHÔNG NGỦ XUYÊN NỀN TẢNG ===
    def start_keep_awake(self):
        """Bắt đầu giữ máy tính không ngủ (Windows & Mac)"""
        if platform.system() == "Windows":
            # Gửi tín hiệu đánh lừa Windows là hệ thống đang bận
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
        else:
            if self.caffeinate_process is None:
                try:
                    self.caffeinate_process = subprocess.Popen(
                        ['caffeinate', '-w', str(os.getpid())],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                except Exception:
                    pass

    def stop_keep_awake(self):
        """Cho phép máy ngủ lại bình thường"""
        if platform.system() == "Windows":
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        else:
            if self.caffeinate_process:
                try:
                    self.caffeinate_process.terminate()
                except Exception:
                    pass
                self.caffeinate_process = None

    def run_process(self):
        if not self.selected_path: messagebox.showwarning("Cảnh báo", "Bạn chưa chọn thư mục!"); return
        if not self.drive_service: messagebox.showwarning("Cảnh báo", "Vui lòng đăng nhập Google Drive trước!"); return
       
        mode = self.mode_var.get()
        if mode == "sync":
            if self.sync_active: self.stop_sync()
            else: self.start_sync()
        elif mode == "download":
            if self.download_sync_active: self.stop_download_sync()
            else: self.start_download_sync()
        else:
            self.is_cancelled = False
            self.is_paused = False
            self.btn_pause.configure(text="⏸️ TẠM DỪNG", fg_color="#F39C12", state="normal")
            self.btn_cancel.configure(state="normal")
            self.btn_start.configure(state="disabled")
            threading.Thread(target=self.logic_process, daemon=True).start()

    def _public_folder(self, folder_id):
        try:
            self.drive_service.permissions().create(fileId=folder_id, body={'type': 'anyone', 'role': 'reader'}, fields='id', supportsAllDrives=True).execute()
        except: pass

    def get_or_create_root_folder(self, folder_name, parent_id):
        if parent_id:
            try:
                parent_meta = self.drive_service.files().get(fileId=parent_id, fields='name, webViewLink', supportsAllDrives=True).execute()
                if parent_meta.get('name') == folder_name:
                    self._public_folder(parent_id)
                    with self.lock:
                        self.entry_link.delete(0, 'end')
                        self.entry_link.insert(0, parent_meta.get('webViewLink'))
                    self.after(0, lambda l=parent_meta.get('webViewLink'): 
                               self.lbl_drive_info.configure(text=f"🔗 Drive đích: {l}"))
                    return parent_id
            except Exception: pass

        query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        if parent_id: query += f" and '{parent_id}' in parents"
        else: query += " and 'root' in parents"

        results = self.drive_service.files().list(q=query, fields="files(id, webViewLink)", supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
        items = results.get('files', [])
        if items:
            root_id = items[0]['id']
            root_link = items[0]['webViewLink']
        else:
            folder_meta = {'name': folder_name, 'mimeType': 'application/vnd.google-apps.folder'}
            if parent_id: folder_meta['parents'] = [parent_id]
            drive_folder = self.drive_service.files().create(body=folder_meta, fields='id, webViewLink', supportsAllDrives=True).execute()
            root_id = drive_folder.get('id')
            root_link = drive_folder.get('webViewLink')

        self._public_folder(root_id)
        with self.lock:
            self.entry_link.delete(0, 'end')
            self.entry_link.insert(0, root_link)
        self.after(0, lambda l=root_link: self.lbl_drive_info.configure(text=f"🔗 Drive đích: {l}"))
        return root_id

    def get_drive_folder_id(self, local_file_path):
        local_dir = os.path.normpath(os.path.dirname(local_file_path))
        with self.folder_lock:
            if local_dir in self.folder_cache: return self.folder_cache[local_dir]
               
            paths_to_create = []
            current_dir = local_dir
            while current_dir not in self.folder_cache:
                if not current_dir.startswith(self.selected_path) and current_dir != os.path.dirname(self.selected_path): break
                paths_to_create.insert(0, current_dir)
                current_dir = os.path.normpath(os.path.dirname(current_dir))
               
            for p in paths_to_create:
                if p in self.folder_cache: continue
                parent_dir = os.path.normpath(os.path.dirname(p))
                parent_id = self.folder_cache.get(parent_dir, self.folder_cache.get(self.selected_path))
               
                query = f"name='{os.path.basename(p)}' and mimeType='application/vnd.google-apps.folder' and '{parent_id}' in parents and trashed=false"
                results = self.drive_service.files().list(q=query, fields="files(id)", supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
                items = results.get('files', [])
               
                if items: self.folder_cache[p] = items[0]['id']
                else:
                    folder_meta = {'name': os.path.basename(p), 'mimeType': 'application/vnd.google-apps.folder', 'parents': [parent_id]}
                    res = self.drive_service.files().create(body=folder_meta, fields='id', supportsAllDrives=True).execute()
                    self.folder_cache[p] = res['id']
            return self.folder_cache.get(local_dir, self.folder_cache.get(self.selected_path))

    def logic_process(self):
        self.start_keep_awake()
        try:
            mode = self.mode_var.get()
            target_id = self.extract_id_from_link(self.entry_drive_id.get().strip()) or None
           
            if mode == "raw_up":
                self.upload_folder_raw(self.selected_path, target_id)
            elif mode == "zip_up":
                files = self.compress_data()
                if files and not self.is_cancelled:
                    try:
                        folder_name = os.path.basename(self.selected_path)
                        zip_root_id = self.get_or_create_root_folder(folder_name, target_id)
                        zip_dir = os.path.dirname(files[0])
                        self.folder_cache = {zip_dir: zip_root_id, self.selected_path: zip_root_id}
                        self.upload_multiple_files_direct(files)
                    except Exception as zip_err:
                        self.update_ui(0, "❌ Lỗi upload sau khi nén", str(zip_err))
                        messagebox.showerror("Lỗi Upload", f"Không thể upload file 7z lên Drive.\n\nChi tiết: {str(zip_err)}", parent=self)
            elif mode == "zip_only":
                self.compress_data()
           
            if self.is_cancelled:
                self.update_ui(0, "❌ ĐÃ HỦY BỎ LUỒNG XỬ LÝ", "File: 0/0")
            elif mode != "download":
                self.update_ui(100, "✅ HOÀN TẤT THÀNH CÔNG", f"Đã xong: {self.total_files}/{self.total_files}")
                messagebox.showinfo("Thành công", "Xử lý hoàn tất đạt tốc độ tối đa!", parent=self)
        except Exception as e:
            self.update_ui(0, "❌ LỖI HỆ THỐNG", str(e))
            messagebox.showerror("Lỗi", f"Chi tiết lỗi: {str(e)}", parent=self)
        finally:
            self.stop_keep_awake()
            self.after(0, lambda: self.btn_start.configure(state="normal"))
            self.after(0, lambda: self.btn_pause.configure(state="disabled"))
            self.after(0, lambda: self.btn_cancel.configure(state="disabled"))

    def upload_multiple_files_direct(self, file_paths_list):
        if not file_paths_list or self.is_cancelled: return
        
        self.start_keep_awake()
        try:
            self.total_files = len(file_paths_list)
            self.completed_files = 0
            self.total_bytes = sum(os.path.getsize(f) for f in file_paths_list)
            self.uploaded_bytes = 0
            self.start_time = time.time()
            self.successfully_uploaded.clear()
           
            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
                futures = [executor.submit(self.upload_worker_direct, f_path) for f_path in file_paths_list]
                for future in concurrent.futures.as_completed(futures):
                    if self.is_cancelled: break
        finally:
            self.stop_keep_awake()

    def upload_worker_direct(self, file_path):
        if self.is_cancelled: return
        while self.is_paused:
            if self.is_cancelled: return
            time.sleep(0.5)
        thread_service = build('drive', 'v3', credentials=self.creds)
        parent_id = self.get_drive_folder_id(file_path)
        body = {'name': os.path.basename(file_path), 'parents': [parent_id]}
       
        media = MediaFileUpload(file_path, chunksize=256 * 1024 * 1024, resumable=True)
        request = thread_service.files().create(body=body, media_body=media, fields='id', supportsAllDrives=True)
       
        last_progress = 0
        while True:
            if self.is_cancelled: return
            while self.is_paused:
                if self.is_cancelled: return
                time.sleep(0.5)
            try:
                status, response = request.next_chunk()
                if status:
                    chunk_delta = status.resumable_progress - last_progress
                    last_progress = status.resumable_progress
                   
                    with self.lock:
                        self.uploaded_bytes += chunk_delta
                        elapsed = time.time() - self.start_time
                        speed = (self.uploaded_bytes / (1024 * 1024)) / elapsed if elapsed > 0 else 0
                        eta = (self.total_bytes - self.uploaded_bytes) / (speed * 1024 * 1024) if speed > 0 else 0
                        percent = (self.uploaded_bytes / self.total_bytes) * 100 if self.total_bytes > 0 else 0
                       
                        self.update_ui(percent, f"🚀 Bơm luồng: {os.path.basename(file_path)}",
                                       f"{speed:.2f} MB/s | ETA: {int(eta // 60)}m {int(eta % 60)}s | File: {self.completed_files}/{self.total_files}")
               
                if response:
                    with self.lock:
                        self.completed_files += 1
                        self.successfully_uploaded.add(file_path)
                       
                        self.update_ui((self.uploaded_bytes / self.total_bytes) * 100 if self.total_bytes > 0 else 100,
                                       f"Hoàn tất: {os.path.basename(file_path)}",
                                       f"{self.lbl_speed.cget('text').split(' | File:')[0]} | File: {self.completed_files}/{self.total_files}")
                    break
            except Exception: break

    def upload_folder_raw(self, folder_path, parent_id):
        self.update_ui(0, "Khởi tạo cấu trúc Maxping...", "Đang quét...")
        folder_name = os.path.basename(folder_path.rstrip('/'))
        root_drive_id = self.get_or_create_root_folder(folder_name, parent_id)
        self.folder_cache = {folder_path: root_drive_id}
       
        upload_tasks = []
        for root, dirs, files in os.walk(folder_path):
            for f in files:
                if not f.startswith('.desi_sync_log') and f not in ['.DS_Store', 'Thumbs.db']:
                    upload_tasks.append(os.path.normpath(os.path.join(root, f)))
        self.upload_multiple_files_direct(upload_tasks)

    def start_download_sync(self):
        self.download_sync_active = True
        self.is_cancelled = False
        self.is_paused = False
        self.downloaded_files_log.clear()
       
        self.btn_pause.configure(state="normal")
        self.btn_cancel.configure(state="normal")
        self.btn_start.configure(text="🔴 DỪNG AUTO DOWNLOAD", fg_color="red")
        threading.Thread(target=self.download_sync_logic, daemon=True).start()

    def stop_download_sync(self):
        self.download_sync_active = False
        self.is_cancelled = True
        self.btn_pause.configure(state="disabled")
        self.btn_cancel.configure(state="disabled")
        self.update_button_label()
        self.update_ui(0, "Đã dừng Auto Download.", "Sẵn sàng...")

    def download_sync_logic(self):
        try:
            drive_id = self.extract_id_from_link(self.entry_drive_id.get().strip())
            if not drive_id:
                messagebox.showerror("Lỗi", "Vui lòng dán Link Drive đích vào ô để tải về!")
                self.stop_download_sync()
                return
               
            self.update_ui(0, "Chế độ Tự Động Kéo File (Trực chờ trên Drive)...", "Đang quét...")
           
            while self.download_sync_active and not self.is_cancelled:
                try:
                    item = self.drive_service.files().get(fileId=drive_id, fields="mimeType, name", supportsAllDrives=True).execute()
                    target_path = os.path.join(self.selected_path, item['name'])
                   
                    download_tasks = []
                    if item['mimeType'] == 'application/vnd.google-apps.folder':
                        self.traverse_and_collect(drive_id, target_path, download_tasks)
                    else:
                        download_tasks.append((drive_id, target_path, int(item.get('size', 0))))
                   
                    new_download_tasks = [t for t in download_tasks if t[1] not in self.downloaded_files_log and not os.path.exists(t[1])]
                   
                    if new_download_tasks:
                        self.update_ui(0, f"Phát hiện {len(new_download_tasks)} file mới trên Drive!", "Đang kéo Max speed...")
                        self.download_multiple_files_parallel(new_download_tasks)
                       
                        if not self.is_cancelled:
                            for t in new_download_tasks:
                                self.downloaded_files_log.add(t[1])
                            self.update_ui(100, "✅ Đã tải xong các file mới hiện tại.", "Đang trực chờ file mới từ Drive...")
                    else:
                        self.update_ui(100, f"Đồng bộ hoàn chỉnh ({len(self.downloaded_files_log)} file).", "Đang trực chờ file mới từ Drive...")
                except Exception: pass
                   
                for _ in range(6):
                    if not self.download_sync_active or self.is_cancelled: break
                    time.sleep(0.5)
        except Exception as e:
            self.update_ui(0, "❌ LỖI AUTO DOWNLOAD", str(e))
            self.stop_download_sync()

    def traverse_and_collect(self, folder_id, local_dir, download_tasks):
        if self.is_cancelled: return
        os.makedirs(local_dir, exist_ok=True)
        page_token = None
        while True:
            results = self.drive_service.files().list(
                q=f"'{folder_id}' in parents and trashed=false",
                fields="nextPageToken, files(id, name, mimeType, size)",
                pageToken=page_token, supportsAllDrives=True, includeItemsFromAllDrives=True
            ).execute()
           
            for item in results.get('files', []):
                path = os.path.join(local_dir, item['name'])
                if item['mimeType'] == 'application/vnd.google-apps.folder':
                    self.traverse_and_collect(item['id'], path, download_tasks)
                else:
                    download_tasks.append((item['id'], path, int(item.get('size', 0))))
            page_token = results.get('nextPageToken')
            if not page_token: break

    def download_multiple_files_parallel(self, tasks):
        if not tasks or self.is_cancelled: return
        
        self.start_keep_awake()
        try:
            self.total_files = len(tasks)
            self.completed_files = 0
            self.total_bytes = sum(size for _, _, size in tasks)
            self.uploaded_bytes = 0
            self.start_time = time.time()
           
            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
                futures = [executor.submit(self.download_worker, f_id, path, size) for f_id, path, size in tasks]
                for future in concurrent.futures.as_completed(futures):
                    if self.is_cancelled: break
        finally:
            self.stop_keep_awake()

    def download_worker(self, file_id, path, file_size):
        if self.is_cancelled: return
        while self.is_paused: time.sleep(0.5)
           
        thread_service = build('drive', 'v3', credentials=self.creds)
        request = thread_service.files().get_media(fileId=file_id, supportsAllDrives=True)
       
        fh = io.FileIO(path, 'wb')
        downloader = MediaIoBaseDownload(fh, request, chunksize=256 * 1024 * 1024)
        done = False
        last_progress = 0
       
        while not done:
            if self.is_cancelled: fh.close(); os.remove(path); return
            status, done = downloader.next_chunk()
            if status:
                chunk_delta = status.resumable_progress - last_progress
                last_progress = status.resumable_progress
                with self.lock:
                    self.uploaded_bytes += chunk_delta
                    elapsed = time.time() - self.start_time
                    speed = (self.uploaded_bytes / (1024 * 1024)) / elapsed if elapsed > 0 else 0
                    eta = (self.total_bytes - self.uploaded_bytes) / (speed * 1024 * 1024) if speed > 0 else 0
                    percent = (self.uploaded_bytes / self.total_bytes) * 100 if self.total_bytes > 0 else 0
                    self.update_ui(percent, f"📥 Đang kéo luồng: {os.path.basename(path)}",
                                   f"{speed:.2f} MB/s | ETA: {int(eta // 60)}m {int(eta % 60)}s | File: {self.completed_files}/{self.total_files}")
        fh.close()
        with self.lock: self.completed_files += 1

    def start_sync(self):
        self.sync_active = True
        self.is_cancelled = False
        self.is_paused = False
        self.btn_pause.configure(state="normal")
        self.btn_cancel.configure(state="normal")
        self.btn_start.configure(text="🔴 DỪNG SYNC", fg_color="red")
        threading.Thread(target=self.sync_logic, daemon=True).start()

    def stop_sync(self):
        self.sync_active = False
        self.is_cancelled = True
        self.btn_pause.configure(state="disabled")
        self.btn_cancel.configure(state="disabled")
        self.update_button_label()
        self.update_ui(0, "Đã DỪNG Auto Sync.", "Sẵn sàng...")

    def get_stable_files_parallel(self, file_list):
        stable_files = []
        try:
            initial_sizes = {f: os.path.getsize(f) for f in file_list}
            for _ in range(6):
                if not self.sync_active or self.is_cancelled: return []
                time.sleep(0.5)
            for f in file_list:
                if os.path.getsize(f) == initial_sizes[f]: stable_files.append(f)
        except: pass
        return stable_files

    def sync_logic(self):
        try:
            target_id = self.extract_id_from_link(self.entry_drive_id.get().strip()) or None
            local_path = self.selected_path
            sync_folder_name = os.path.basename(local_path.rstrip('/'))
            sync_root_id = self.get_or_create_root_folder(sync_folder_name, target_id)
            self.folder_cache = {local_path: sync_root_id}
           
            log_file_name = f".desi_sync_log_{sync_root_id}.txt"
            log_file_path = os.path.join(local_path, log_file_name)
           
            while self.sync_active and not self.is_cancelled:
                known_files = set()
                if os.path.exists(log_file_path):
                    with open(log_file_path, "r", encoding="utf-8") as f: known_files = set(f.read().splitlines())
                current_files = set()
                for root, dirs, files in os.walk(local_path):
                    for f in files:
                        if not f.startswith('.desi_sync_log') and f not in ['.DS_Store', 'Thumbs.db']:
                            current_files.add(os.path.normpath(os.path.join(root, f)))
               
                new_files = list(current_files - known_files)
                if new_files:
                    stable_files = self.get_stable_files_parallel(new_files)
                    if stable_files and self.sync_active and not self.is_cancelled:
                        self.upload_multiple_files_direct(stable_files)
                        if self.successfully_uploaded and self.sync_active and not self.is_cancelled:
                            with open(log_file_path, "a", encoding="utf-8") as log_f:
                                with self.lock:
                                    for f in self.successfully_uploaded:
                                        if f not in known_files:
                                            log_f.write(f + "\n")
                                            known_files.add(f)
                            self.update_ui(100, "✅ Đã đồng bộ hoàn chỉnh!", "Trực chờ file mới...")
                else:
                    if self.sync_active and not self.is_cancelled:
                        self.update_ui(100, f"Đã đồng bộ {len(known_files)} file...", "Trực chờ file mới...")
                for _ in range(6):
                    if not self.sync_active or self.is_cancelled: break
                    time.sleep(0.5)
        except Exception as e:
            self.update_ui(0, "❌ LỖI KHI SYNC", str(e))
            self.stop_sync()

    def compress_data(self):
        if self.is_cancelled: return []
        self.update_ui(10, "Đang nén dữ liệu bằng 7-Zip...", "Hệ thống đang xử lý...")
        folder_name = os.path.basename(self.selected_path)
        parent_dir = os.path.dirname(self.selected_path)
        archive_base = os.path.join(parent_dir, folder_name)
       
        split_opt = self.opt_split.get()
        
        # === TỰ ĐỘNG NHẬN DIỆN FILE THỰC THI 7-ZIP THEO HỆ ĐIỀU HÀNH ===
        if platform.system() == "Windows":
            sevenzip_cmd = get_resource_path(os.path.join("bin", "windows", "7z.exe"))
        else:
            sevenzip_cmd = get_resource_path("7zz")
            
        if not os.path.exists(sevenzip_cmd):
            # Fallback nếu mất file tĩnh, sử dụng biến môi trường hệ thống
            sevenzip_cmd = "7z" if platform.system() == "Windows" else "7zz"
        
        cmd = [sevenzip_cmd, 'a', '-mx=0', '-mmt=on', f"{archive_base}.7z", self.selected_path]
        if split_opt != "Không chia":
            parts = int(split_opt.split()[1])
            size_bytes = sum(os.path.getsize(os.path.join(dp, f)) for dp, dn, filenames in os.walk(self.selected_path) for f in filenames)
            part_size_mb = (size_bytes // parts) // (1024*1024) + 100
            cmd.append(f"-v{part_size_mb}m")
           
        process = subprocess.Popen(cmd)
        while process.poll() is None:
            if self.is_cancelled: process.terminate(); return []
            time.sleep(1)
           
        if split_opt == "Không chia": files = [f"{archive_base}.7z"]
        else: files = sorted(glob.glob(f"{archive_base}.7z.*"))
           
        if not self.is_cancelled:
            self.update_ui(100, "✅ NÉN XONG THÀNH CÔNG!", "Lưu trữ tại ổ cứng nội bộ")
        return files

if __name__ == "__main__":
    app = AutoDriveDESI()
    app.mainloop()