"""
Base Module Class for ORCA Software Suite
Defines the interface that all modules must implement.
"""

from abc import ABC, abstractmethod
from tkinter import ttk


class BaseModule(ABC):
    """Base class for all ORCA suite modules."""
    
    def __init__(self, parent_frame):
        """
        Initialize the module.
        
        Args:
            parent_frame: The parent frame where the module's UI will be placed
        """
        self.parent_frame = parent_frame
        self.main_frame = None
        self.is_active = False
    
    @abstractmethod
    def get_name(self) -> str:
        """Return the display name of the module."""
        pass
    
    @abstractmethod
    def get_icon(self) -> str:
        """Return an icon/emoji for the module (for UI display)."""
        pass
    
    @abstractmethod
    def create_ui(self):
        """Create and initialize the module's user interface."""
        pass
    
    def activate(self):
        """Activate the module (called when module is selected)."""
        if not self.is_active:
            if self.main_frame is None:
                self.create_ui()
            self.main_frame.pack(fill='both', expand=True)
            self.is_active = True
            try:
                top = self.parent_frame.winfo_toplevel()
                app = getattr(top, "_orca_app", None)
                if app and hasattr(self, "apply_app_theme"):
                    from modules import app_theme
                    self.apply_app_theme(
                        app_theme.build_context(app.theme_mode, app.editor_font_pt)
                    )
            except Exception:
                pass
    
    def deactivate(self):
        """Deactivate the module (called when another module is selected)."""
        if self.main_frame is not None:
            self.main_frame.pack_forget()
        self.is_active = False
    
    def cleanup(self):
        """Clean up resources when module is removed."""
        self.deactivate()
        if self.main_frame is not None:
            self.main_frame.destroy()
            self.main_frame = None


