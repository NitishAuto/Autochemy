import sys
import subprocess
import threading
import tkinter as tk
from tkinter import ttk

def install_packages(req_file, window):
    success = False
    try:
        # Silently upgrade pip first
        subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"], 
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Silently install requirements
        result = subprocess.run([sys.executable, "-m", "pip", "install", "-r", req_file], 
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        if result.returncode == 0:
            success = True
    except Exception:
        pass
        
    # Tell UI thread to close and exit with appropriate code
    window.after(0, lambda: finish(window, success))

def finish(window, success):
    window.destroy()
    sys.exit(0 if success else 1)

def main():
    req_file = sys.argv[1] if len(sys.argv) > 1 else "requirements.txt"
    
    root = tk.Tk()
    root.title("AutoChemy Setup")
    
    # Calculate screen center for window
    w, h = 450, 200
    ws = root.winfo_screenwidth()
    hs = root.winfo_screenheight()
    x = int((ws/2) - (w/2))
    y = int((hs/2) - (h/2))
    root.geometry(f'{w}x{h}+{x}+{y}')
    
    # Borderless window for a splash screen look
    root.overrideredirect(True) 
    
    # Main container with a blue border
    frame = tk.Frame(root, bg="#ffffff", highlightbackground="#0b5cab", highlightthickness=2)
    frame.pack(fill=tk.BOTH, expand=True)
    
    style = ttk.Style()
    if "clam" in style.theme_names():
        style.theme_use("clam")
        
    content = tk.Frame(frame, bg="#ffffff")
    content.pack(expand=True, fill=tk.BOTH, padx=30, pady=25)
    
    lbl_title = tk.Label(content, text="🧪 AutoChemy Setup", font=("Segoe UI", 18, "bold"), fg="#0b5cab", bg="#ffffff")
    lbl_title.pack(pady=(0, 10))
    
    lbl_desc = tk.Label(content, text="Installing required libraries (Pandas, Numpy, etc.)\nThis may take a few minutes depending on your internet speed.", 
                        font=("Segoe UI", 10), bg="#ffffff", fg="#555555", justify=tk.CENTER)
    lbl_desc.pack(pady=(0, 15))
    
    # Animated text frames mimicking a GIF
    frames = [
        "⚗️ Synthesizing packages .  ",
        "⚗️ Synthesizing packages .. ",
        "⚗️ Synthesizing packages ...",
        "⚗️ Synthesizing packages  . "
    ]
    lbl_anim = tk.Label(content, text=frames[0], font=("Segoe UI", 12, "bold"), bg="#ffffff", fg="#e67e22")
    lbl_anim.pack(pady=(0, 15))
    
    def update_anim(index):
        lbl_anim.config(text=frames[index % len(frames)])
        root.after(400, update_anim, index + 1)
        
    update_anim(0)
    
    progress = ttk.Progressbar(content, mode='indeterminate', length=350)
    progress.pack()
    progress.start(10)
    
    # Start the installation in a background thread so UI stays responsive
    t = threading.Thread(target=install_packages, args=(req_file, root))
    t.daemon = True
    t.start()
    
    root.mainloop()

if __name__ == "__main__":
    main()
