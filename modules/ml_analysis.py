"""
ML Studio Module
Regression workbench for chemistry descriptors: load a CSV, pick target and
features, select descriptors (SIS / distance correlation / mutual information),
benchmark many models with cross-validation, inspect diagnostics, and predict
new data with a saved model.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import os
import queue
import threading
import time
import traceback
import warnings

import numpy as np
import pandas as pd

from modules.base_module import BaseModule
from modules import app_theme

try:
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    _MPL_OK = True
except Exception:
    _MPL_OK = False

_ML_IMPORT_ERROR = None
try:
    from sklearn.base import clone
    from sklearn.model_selection import train_test_split, KFold, GridSearchCV, learning_curve
    from sklearn.preprocessing import MinMaxScaler, StandardScaler, RobustScaler
    from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score, max_error
    from sklearn.pipeline import Pipeline
    from sklearn.linear_model import LinearRegression, Ridge, Lasso, ElasticNet
    from sklearn.neighbors import KNeighborsRegressor
    from sklearn.tree import DecisionTreeRegressor
    from sklearn.ensemble import RandomForestRegressor, ExtraTreesRegressor, GradientBoostingRegressor
    from sklearn.svm import SVR
    from sklearn.kernel_ridge import KernelRidge
    from sklearn.gaussian_process import GaussianProcessRegressor
    from sklearn.gaussian_process.kernels import RBF, WhiteKernel, ConstantKernel
    from sklearn.neural_network import MLPRegressor
    from sklearn.feature_selection import mutual_info_regression
    from sklearn.inspection import permutation_importance
    import joblib
except Exception as exc:
    _ML_IMPORT_ERROR = exc

try:
    from xgboost import XGBRegressor
    _XGB_OK = True
except Exception:
    _XGB_OK = False


TARGET_TRANSFORMS = {
    "None": (lambda y: y, lambda y: y, ""),
    "kcal/mol → eV (÷ 23.06)": (lambda y: y / 23.06, lambda y: y * 23.06, "eV"),
    "eV → kcal/mol (× 23.06)": (lambda y: y * 23.06, lambda y: y / 23.06, "kcal/mol"),
    "Eh → kcal/mol (× 627.51)": (lambda y: y * 627.509474, lambda y: y / 627.509474, "kcal/mol"),
    "kJ/mol → kcal/mol (÷ 4.184)": (lambda y: y / 4.184, lambda y: y * 4.184, "kcal/mol"),
    "log10(y)": (lambda y: np.log10(y), lambda y: 10 ** y, "log10"),
}

FEATURE_SELECTORS = ["None (use all)", "SIS (|Pearson r|)", "Distance correlation", "Mutual information"]
SCALERS = ["Standard", "MinMax", "Robust", "None"]

MODEL_NAMES = [
    "Linear Regression", "Ridge", "Lasso", "Elastic Net",
    "k-Nearest Neighbours", "Decision Tree", "Random Forest", "Extra Trees",
    "Gradient Boosting", "SVR", "Kernel Ridge", "Gaussian Process",
    "MLP (Neural Net)", "XGBoost",
]
DEFAULT_MODELS = {"Linear Regression", "Random Forest", "SVR", "XGBoost", "Gradient Boosting", "Ridge"}


def _model_zoo(seed):
    """name -> (estimator, small hyper-parameter grid for optional tuning)."""
    trees = {"model__n_estimators": [200, 500], "model__max_depth": [None, 8]}
    zoo = {
        "Linear Regression": (LinearRegression(), {}),
        "Ridge": (Ridge(random_state=seed), {"model__alpha": [0.01, 0.1, 1, 10, 100]}),
        "Lasso": (Lasso(random_state=seed, max_iter=20000), {"model__alpha": [1e-4, 1e-3, 1e-2, 0.1, 1]}),
        "Elastic Net": (ElasticNet(random_state=seed, max_iter=20000),
                        {"model__alpha": [1e-3, 1e-2, 0.1, 1], "model__l1_ratio": [0.2, 0.5, 0.8]}),
        "k-Nearest Neighbours": (KNeighborsRegressor(),
                                 {"model__n_neighbors": [3, 5, 7, 11], "model__weights": ["uniform", "distance"]}),
        "Decision Tree": (DecisionTreeRegressor(random_state=seed), {"model__max_depth": [3, 5, 8, None]}),
        "Random Forest": (RandomForestRegressor(random_state=seed, n_jobs=-1), trees),
        "Extra Trees": (ExtraTreesRegressor(random_state=seed, n_jobs=-1), trees),
        "Gradient Boosting": (GradientBoostingRegressor(random_state=seed),
                              {"model__n_estimators": [100, 300], "model__learning_rate": [0.03, 0.1],
                               "model__max_depth": [2, 3]}),
        "SVR": (SVR(), {"model__C": [0.1, 1, 10, 100], "model__gamma": ["scale", 0.01, 0.1]}),
        "Kernel Ridge": (KernelRidge(kernel="rbf"),
                         {"model__alpha": [1e-3, 1e-2, 0.1, 1], "model__gamma": [0.01, 0.1, 1]}),
        "Gaussian Process": (GaussianProcessRegressor(kernel=ConstantKernel() * RBF() + WhiteKernel(),
                                                      normalize_y=True, random_state=seed), {}),
        "MLP (Neural Net)": (MLPRegressor(hidden_layer_sizes=(64, 64), max_iter=3000, random_state=seed),
                             {"model__alpha": [1e-4, 1e-3, 1e-2], "model__hidden_layer_sizes": [(32,), (64, 64)]}),
    }
    if _XGB_OK:
        zoo["XGBoost"] = (XGBRegressor(random_state=seed, n_estimators=300, learning_rate=0.05, max_depth=4,
                                       verbosity=0),
                          {"model__max_depth": [3, 5], "model__learning_rate": [0.03, 0.1],
                           "model__n_estimators": [200, 500]})
    return zoo


def _make_scaler(name):
    return {"Standard": StandardScaler(), "MinMax": MinMaxScaler(), "Robust": RobustScaler()}.get(name, "passthrough")


# ================= FEATURE SELECTION =================
def sis(X, y, n_features):
    """Sure Independence Screening: keep the n features with the largest |Pearson r|."""
    corr = X.corrwith(y).abs().fillna(0)
    top_features = corr.sort_values(ascending=False).head(n_features).index
    return X[top_features]


def _distance_correlation(x, y):
    x = np.asarray(x, float)[:, None]
    y = np.asarray(y, float)[:, None]
    a = np.abs(x - x.T)
    b = np.abs(y - y.T)
    A = a - a.mean(0) - a.mean(1)[:, None] + a.mean()
    B = b - b.mean(0) - b.mean(1)[:, None] + b.mean()
    dcov = (A * B).mean()
    dvx, dvy = (A * A).mean(), (B * B).mean()
    if dvx <= 0 or dvy <= 0:
        return 0.0
    return float(np.sqrt(max(dcov, 0) / np.sqrt(dvx * dvy)))


def feature_scores(X, y, method, seed=0):
    """Relevance score per column of X (higher = more relevant)."""
    if method.startswith("SIS"):
        return X.corrwith(y).abs().fillna(0)
    if method.startswith("Distance"):
        Xs = X if len(X) <= 3000 else X.sample(3000, random_state=seed)
        ys = y.loc[Xs.index]
        return pd.Series({c: _distance_correlation(Xs[c].values, ys.values) for c in X.columns})
    if method.startswith("Mutual"):
        return pd.Series(mutual_info_regression(X.values, y.values, random_state=seed), index=X.columns)
    return pd.Series(1.0, index=X.columns)


def _metrics(y_true, y_pred):
    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)
    return {
        "rmse": float(np.sqrt(mean_squared_error(y_true, y_pred))),
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "r2": float(r2_score(y_true, y_pred)) if len(y_true) > 1 else float("nan"),
        "maxerr": float(max_error(y_true, y_pred)),
    }


class _PlotPanel:
    def __init__(self, parent, figsize=(6, 4)):
        self.frame = ttk.Frame(parent)
        self.fig = Figure(figsize=figsize, dpi=100)
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.frame)
        tb = ttk.Frame(self.frame)
        tb.pack(side=tk.BOTTOM, fill=tk.X)
        self.toolbar = NavigationToolbar2Tk(self.canvas, tb, pack_toolbar=False)
        self.toolbar.update()
        self.toolbar.pack(side=tk.LEFT)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    def begin(self, p):
        self.fig.clear()
        self.fig.set_facecolor(p["bg_main"])

    @staticmethod
    def style(ax, p):
        ax.set_facecolor(p["tree_bg"])
        for s in ax.spines.values():
            s.set_color(p["border"])
        ax.tick_params(colors=p["fg"], labelsize=9)
        ax.xaxis.label.set_color(p["fg"])
        ax.yaxis.label.set_color(p["fg"])
        ax.title.set_color(p["fg"])
        ax.grid(True, linestyle="--", alpha=0.3, color=p["fg_muted"])

    def legend(self, ax, p, **kw):
        leg = ax.legend(frameon=False, fontsize=8, **kw)
        for t in leg.get_texts():
            t.set_color(p["fg"])

    def empty(self, p, msg):
        self.begin(p)
        ax = self.fig.add_subplot(111)
        self.style(ax, p)
        ax.grid(False)
        ax.set_xticks([])
        ax.set_yticks([])
        ax.text(0.5, 0.5, msg, ha="center", va="center", color=p["fg_muted"], fontsize=11, transform=ax.transAxes)
        self.canvas.draw_idle()

    def finish(self):
        try:
            self.fig.tight_layout()
        except Exception:
            pass
        self.canvas.draw_idle()


# ============================================================
class MLAnalysisModule(BaseModule):

    def __init__(self, parent_frame):
        super().__init__(parent_frame)
        self.df = None
        self.results = {}
        self.run_config = None
        self.loaded_model = None
        self.pred_df = None
        self._queue = queue.Queue()
        self._worker = None
        self._stop_flag = threading.Event()
        self._mode = "light"
        self.model_vars = {}

    def get_name(self):
        return "ML"

    def _p(self):
        return app_theme.PALETTES.get(self._mode, app_theme.PALETTES["light"])

    # ============================================================
    def create_ui(self):
        top = self.parent_frame.winfo_toplevel()
        app = getattr(top, "_orca_app", None)
        if app is not None:
            self._mode = getattr(app, "theme_mode", "light")

        self.main_frame = ttk.Frame(self.parent_frame, padding=10)

        header = ttk.Frame(self.main_frame)
        header.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(header, text="Machine Learning Studio", font=("Segoe UI", 16, "bold")).pack(side=tk.LEFT)
        ttk.Button(header, text="📂 Load CSV", command=self.browse).pack(side=tk.LEFT, padx=(15, 4))
        ttk.Button(header, text="Save model…", command=self.save_model).pack(side=tk.LEFT, padx=4)
        ttk.Button(header, text="Load model…", command=self.load_model).pack(side=tk.LEFT, padx=4)
        ttk.Button(header, text="Export results…", command=self.export_results).pack(side=tk.LEFT, padx=4)
        self.status_var = tk.StringVar(value="Load a CSV of descriptors to begin.")
        ttk.Label(header, textvariable=self.status_var, font=("Segoe UI", 9, "italic")).pack(side=tk.RIGHT)

        pane = ttk.PanedWindow(self.main_frame, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True)
        left_outer = ttk.Frame(pane)
        right = ttk.Frame(pane)
        pane.add(left_outer, weight=0)
        pane.add(right, weight=1)

        # scrollable settings column
        canvas = tk.Canvas(left_outer, width=380, highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(left_outer, orient="vertical", command=canvas.yview)
        left = ttk.Frame(canvas)
        left.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        win = canvas.create_window((0, 0), window=left, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._settings_canvas = canvas
        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", self._on_wheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))

        self._build_dataset_box(left)
        self._build_selection_box(left)
        self._build_models_box(left)
        self._build_training_box(left)

        run_row = ttk.Frame(left)
        run_row.pack(fill=tk.X, pady=(6, 4), padx=2)
        self.run_btn = tk.Button(run_row, text="▶  Train & Evaluate", bg="#0284c7", fg="#ffffff",
                                 activebackground="#0369a1", activeforeground="#ffffff",
                                 font=("Segoe UI", 11, "bold"), relief=tk.FLAT, pady=6, cursor="hand2",
                                 command=self.run)
        self.run_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(run_row, text="Stop", command=self._request_stop).pack(side=tk.LEFT, padx=(6, 0))
        self.progress = ttk.Progressbar(left, mode="determinate")
        self.progress.pack(fill=tk.X, padx=2, pady=(2, 8))

        # right notebook
        self.nb = ttk.Notebook(right)
        self.nb.pack(fill=tk.BOTH, expand=True)
        for attr, title, builder in (
            ("tab_data", "Data", self._build_data_tab),
            ("tab_board", "Leaderboard", self._build_board_tab),
            ("tab_diag", "Diagnostics", self._build_diag_tab),
            ("tab_pred", "Predict", self._build_predict_tab),
            ("tab_log", "Log", self._build_log_tab),
        ):
            f = ttk.Frame(self.nb, padding=8)
            setattr(self, attr, f)
            self.nb.add(f, text=title)
            builder(f)

        self._configure_tags()
        self._draw_data_plot()
        self._draw_board_plot()
        self._draw_diag()

    def _on_wheel(self, e):
        self._settings_canvas.yview_scroll(int(-e.delta / 120), "units")

    # ---------------- settings boxes ----------------
    def _build_dataset_box(self, parent):
        box = ttk.LabelFrame(parent, text="1 · Dataset", padding=8)
        box.pack(fill=tk.X, pady=4, padx=2)
        row = ttk.Frame(box)
        row.pack(fill=tk.X)
        self.file_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.file_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(row, text="Browse", command=self.browse).pack(side=tk.LEFT, padx=(4, 0))
        self.data_info_var = tk.StringVar(value="No data loaded")
        ttk.Label(box, textvariable=self.data_info_var, font=("Segoe UI", 8), wraplength=340).pack(anchor="w", pady=(4, 6))

        g = ttk.Frame(box)
        g.pack(fill=tk.X)
        ttk.Label(g, text="Target (y)").grid(row=0, column=0, sticky="w")
        self.target_var = tk.StringVar()
        self.target_cb = ttk.Combobox(g, textvariable=self.target_var, state="readonly", width=26)
        self.target_cb.grid(row=0, column=1, sticky="ew", padx=4, pady=2)
        self.target_cb.bind("<<ComboboxSelected>>", lambda _e: self._on_target_changed())
        ttk.Label(g, text="Transform").grid(row=1, column=0, sticky="w")
        self.transform_var = tk.StringVar(value="None")
        ttk.Combobox(g, textvariable=self.transform_var, values=list(TARGET_TRANSFORMS), state="readonly",
                     width=26).grid(row=1, column=1, sticky="ew", padx=4, pady=2)
        ttk.Label(g, text="Missing values").grid(row=2, column=0, sticky="w")
        self.nan_var = tk.StringVar(value="Impute median")
        ttk.Combobox(g, textvariable=self.nan_var, values=["Impute median", "Drop rows"], state="readonly",
                     width=26).grid(row=2, column=1, sticky="ew", padx=4, pady=2)
        g.grid_columnconfigure(1, weight=1)

        hdr = ttk.Frame(box)
        hdr.pack(fill=tk.X, pady=(6, 2))
        ttk.Label(hdr, text="Candidate features (X)").pack(side=tk.LEFT)
        ttk.Button(hdr, text="None", width=5, command=lambda: self.feat_list.selection_clear(0, tk.END)).pack(side=tk.RIGHT)
        ttk.Button(hdr, text="All", width=5, command=lambda: self.feat_list.selection_set(0, tk.END)).pack(side=tk.RIGHT, padx=2)
        lf = ttk.Frame(box)
        lf.pack(fill=tk.X)
        self.feat_list = tk.Listbox(lf, selectmode=tk.EXTENDED, height=8, exportselection=False,
                                    font=("Segoe UI", 9), activestyle="none")
        fsb = ttk.Scrollbar(lf, orient="vertical", command=self.feat_list.yview)
        self.feat_list.configure(yscrollcommand=fsb.set)
        self.feat_list.pack(side=tk.LEFT, fill=tk.X, expand=True)
        fsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.feat_list.bind("<<ListboxSelect>>", lambda _e: self._update_feat_count())
        self.feat_count_var = tk.StringVar(value="")
        ttk.Label(box, textvariable=self.feat_count_var, font=("Segoe UI", 8)).pack(anchor="w")

    def _build_selection_box(self, parent):
        box = ttk.LabelFrame(parent, text="2 · Feature selection & scaling", padding=8)
        box.pack(fill=tk.X, pady=4, padx=2)
        self.selector_var = tk.StringVar(value=FEATURE_SELECTORS[1])
        self.n_features = tk.IntVar(value=5)
        self.scaler_var = tk.StringVar(value="Standard")
        g = ttk.Frame(box)
        g.pack(fill=tk.X)
        ttk.Label(g, text="Method").grid(row=0, column=0, sticky="w")
        ttk.Combobox(g, textvariable=self.selector_var, values=FEATURE_SELECTORS, state="readonly",
                     width=24).grid(row=0, column=1, sticky="ew", padx=4, pady=2)
        ttk.Label(g, text="Keep top N").grid(row=1, column=0, sticky="w")
        ttk.Spinbox(g, from_=1, to=500, textvariable=self.n_features, width=6).grid(row=1, column=1, sticky="w", padx=4, pady=2)
        ttk.Label(g, text="Scaler").grid(row=2, column=0, sticky="w")
        ttk.Combobox(g, textvariable=self.scaler_var, values=SCALERS, state="readonly",
                     width=24).grid(row=2, column=1, sticky="ew", padx=4, pady=2)
        g.grid_columnconfigure(1, weight=1)
        ttk.Button(box, text="Preview feature ranking", command=self.preview_ranking).pack(anchor="w", pady=(6, 0))
        ttk.Label(box, text="Selection runs on the training split only (no test-set leakage).",
                  font=("Segoe UI", 8, "italic")).pack(anchor="w", pady=(4, 0))

    def _build_models_box(self, parent):
        box = ttk.LabelFrame(parent, text="3 · Models", padding=8)
        box.pack(fill=tk.X, pady=4, padx=2)
        grid = ttk.Frame(box)
        grid.pack(fill=tk.X)
        for i, name in enumerate(MODEL_NAMES):
            var = tk.BooleanVar(value=name in DEFAULT_MODELS)
            cb = ttk.Checkbutton(grid, text=name, variable=var)
            cb.grid(row=i // 2, column=i % 2, sticky="w", padx=2, pady=1)
            if name == "XGBoost" and not _XGB_OK:
                var.set(False)
                cb.state(["disabled"])
                cb.config(text="XGBoost (not installed)")
            self.model_vars[name] = var
        # legacy attribute names kept for saved sessions
        self.use_lr = self.model_vars["Linear Regression"]
        self.use_rf = self.model_vars["Random Forest"]
        self.use_svr = self.model_vars["SVR"]
        self.use_xgb = self.model_vars["XGBoost"]
        row = ttk.Frame(box)
        row.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(row, text="All", width=5, command=lambda: self._set_models(True)).pack(side=tk.LEFT)
        ttk.Button(row, text="None", width=5, command=lambda: self._set_models(False)).pack(side=tk.LEFT, padx=2)
        ttk.Button(row, text="Fast set", command=lambda: self._set_models(
            {"Linear Regression", "Ridge", "Random Forest", "SVR"})).pack(side=tk.LEFT, padx=2)

    def _set_models(self, which):
        for name, var in self.model_vars.items():
            if name == "XGBoost" and not _XGB_OK:
                continue
            var.set(which if isinstance(which, bool) else name in which)

    def _build_training_box(self, parent):
        box = ttk.LabelFrame(parent, text="4 · Training", padding=8)
        box.pack(fill=tk.X, pady=4, padx=2)
        self.cv_folds = tk.IntVar(value=10)
        self.test_size = tk.DoubleVar(value=0.1)
        self.seed_var = tk.IntVar(value=108)
        self.tune_var = tk.BooleanVar(value=False)
        self.test_mode = tk.StringVar(value="Average of CV-fold models")
        g = ttk.Frame(box)
        g.pack(fill=tk.X)
        ttk.Label(g, text="CV folds").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(g, from_=2, to=50, textvariable=self.cv_folds, width=6).grid(row=0, column=1, sticky="w", padx=4, pady=2)
        ttk.Label(g, text="Test fraction").grid(row=1, column=0, sticky="w")
        ttk.Spinbox(g, from_=0.0, to=0.5, increment=0.05, textvariable=self.test_size, width=6).grid(row=1, column=1, sticky="w", padx=4, pady=2)
        ttk.Label(g, text="Random seed").grid(row=2, column=0, sticky="w")
        ttk.Spinbox(g, from_=0, to=99999, textvariable=self.seed_var, width=8).grid(row=2, column=1, sticky="w", padx=4, pady=2)
        ttk.Label(g, text="Test prediction").grid(row=3, column=0, sticky="w")
        ttk.Combobox(g, textvariable=self.test_mode, state="readonly", width=24,
                     values=["Average of CV-fold models", "Refit on full training set"]).grid(row=3, column=1, sticky="ew", padx=4, pady=2)
        g.grid_columnconfigure(1, weight=1)
        ttk.Checkbutton(box, text="Hyper-parameter tuning (grid search, slower)",
                        variable=self.tune_var).pack(anchor="w", pady=(4, 0))

    # ---------------- right tabs ----------------
    def _tree(self, parent, cols, widths, height=10):
        f = ttk.Frame(parent)
        t = ttk.Treeview(f, columns=cols, show="headings", height=height)
        for c, w in zip(cols, widths):
            t.heading(c, text=c)
            t.column(c, width=w, anchor="e" if w < 160 else "w")
        sy = ttk.Scrollbar(f, orient="vertical", command=t.yview)
        sx = ttk.Scrollbar(f, orient="horizontal", command=t.xview)
        t.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        sx.pack(side=tk.BOTTOM, fill=tk.X)
        t.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sy.pack(side=tk.RIGHT, fill=tk.Y)
        return f, t

    def _build_data_tab(self, f):
        pane = ttk.PanedWindow(f, orient=tk.VERTICAL)
        pane.pack(fill=tk.BOTH, expand=True)
        top = ttk.Frame(pane)
        bottom = ttk.Frame(pane)
        pane.add(top, weight=1)
        pane.add(bottom, weight=2)
        self.preview_holder = ttk.Frame(top)
        self.preview_holder.pack(fill=tk.BOTH, expand=True)
        self.preview_tree = None
        ttk.Label(self.preview_holder, text="Load a CSV file to preview the data (first 200 rows).",
                  font=("Segoe UI", 10)).pack(pady=20)

        ctrl = ttk.Frame(bottom)
        ctrl.pack(fill=tk.X, pady=(4, 2))
        ttk.Label(ctrl, text="Plot:").pack(side=tk.LEFT)
        self.data_plot_kind = tk.StringVar(value="Correlation with target")
        cb = ttk.Combobox(ctrl, textvariable=self.data_plot_kind, state="readonly", width=26,
                          values=["Correlation with target", "Correlation heatmap", "Target distribution",
                                  "Feature vs target"])
        cb.pack(side=tk.LEFT, padx=4)
        cb.bind("<<ComboboxSelected>>", lambda _e: self._draw_data_plot())
        self.scatter_feat = tk.StringVar()
        self.scatter_cb = ttk.Combobox(ctrl, textvariable=self.scatter_feat, state="readonly", width=24)
        self.scatter_cb.pack(side=tk.LEFT, padx=4)
        self.scatter_cb.bind("<<ComboboxSelected>>", lambda _e: self._draw_data_plot())
        self.data_plot = _PlotPanel(bottom, (6, 3.6)) if _MPL_OK else None
        if self.data_plot:
            self.data_plot.frame.pack(fill=tk.BOTH, expand=True)

    def _build_board_tab(self, f):
        cols = ("Rank", "Model", "CV RMSE", "CV MAE", "CV R²", "Test RMSE", "Test MAE", "Test R²", "Max |err|", "Time (s)")
        tf, self.board_tree = self._tree(f, cols, (45, 170, 85, 85, 70, 85, 85, 70, 80, 70), height=9)
        tf.pack(fill=tk.X)
        self.board_tree.bind("<Double-1>", self._board_open_diag)
        self.board_info_var = tk.StringVar(value="Train models to populate the leaderboard. Double-click a row for diagnostics.")
        ttk.Label(f, textvariable=self.board_info_var, font=("Segoe UI", 9), wraplength=900).pack(anchor="w", pady=4)
        ctrl = ttk.Frame(f)
        ctrl.pack(fill=tk.X)
        ttk.Label(ctrl, text="Compare by:").pack(side=tk.LEFT)
        self.board_metric = tk.StringVar(value="RMSE")
        cb = ttk.Combobox(ctrl, textvariable=self.board_metric, values=["RMSE", "MAE", "R²"], state="readonly", width=8)
        cb.pack(side=tk.LEFT, padx=4)
        cb.bind("<<ComboboxSelected>>", lambda _e: self._draw_board_plot())
        self.board_plot = _PlotPanel(f, (6, 3.4)) if _MPL_OK else None
        if self.board_plot:
            self.board_plot.frame.pack(fill=tk.BOTH, expand=True)

    def _build_diag_tab(self, f):
        ctrl = ttk.Frame(f)
        ctrl.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(ctrl, text="Model").pack(side=tk.LEFT)
        self.diag_model = tk.StringVar()
        self.diag_model_cb = ttk.Combobox(ctrl, textvariable=self.diag_model, state="readonly", width=24)
        self.diag_model_cb.pack(side=tk.LEFT, padx=4)
        self.diag_model_cb.bind("<<ComboboxSelected>>", lambda _e: self._draw_diag())
        ttk.Label(ctrl, text="View").pack(side=tk.LEFT, padx=(10, 0))
        self.diag_kind = tk.StringVar(value="Parity plot")
        cb = ttk.Combobox(ctrl, textvariable=self.diag_kind, state="readonly", width=24,
                          values=["Parity plot", "Residuals vs predicted", "Residual histogram",
                                  "Feature importance (permutation)", "Learning curve", "All models parity"])
        cb.pack(side=tk.LEFT, padx=4)
        cb.bind("<<ComboboxSelected>>", lambda _e: self._draw_diag())
        self.diag_info_var = tk.StringVar(value="")
        ttk.Label(ctrl, textvariable=self.diag_info_var, font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=10)
        self.diag_plot = _PlotPanel(f, (6, 4.6)) if _MPL_OK else None
        if self.diag_plot:
            self.diag_plot.frame.pack(fill=tk.BOTH, expand=True)

    def _build_predict_tab(self, f):
        top = ttk.LabelFrame(f, text="Predict new samples", padding=8)
        top.pack(fill=tk.X)
        row = ttk.Frame(top)
        row.pack(fill=tk.X)
        ttk.Label(row, text="Model").pack(side=tk.LEFT)
        self.pred_model = tk.StringVar()
        self.pred_model_cb = ttk.Combobox(row, textvariable=self.pred_model, state="readonly", width=30)
        self.pred_model_cb.pack(side=tk.LEFT, padx=4)
        ttk.Button(row, text="Predict CSV…", command=self.predict_csv).pack(side=tk.LEFT, padx=4)
        ttk.Button(row, text="Export predictions…", command=self.export_predictions).pack(side=tk.LEFT, padx=4)
        ttk.Label(top, text="The CSV must contain the model's feature columns. Models trained here are refit on the "
                            "full training split; saved models (.joblib) can be loaded from the header.",
                  font=("Segoe UI", 8, "italic"), wraplength=900).pack(anchor="w", pady=(6, 0))
        self.pred_info_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.pred_info_var, font=("Segoe UI", 9)).pack(anchor="w", pady=4)
        self.pred_holder = ttk.Frame(f)
        self.pred_holder.pack(fill=tk.BOTH, expand=True)

    def _build_log_tab(self, f):
        self.output = tk.Text(f, height=18, wrap=tk.WORD, font=("Consolas", 10))
        sy = ttk.Scrollbar(f, orient="vertical", command=self.output.yview)
        self.output.configure(yscrollcommand=sy.set)
        self.output.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sy.pack(side=tk.RIGHT, fill=tk.Y)

    # ============================================================
    # Theme & session
    # ============================================================
    def _configure_tags(self):
        good = "#4ade80" if self._mode == "dark" else "#15803d"
        self.board_tree.tag_configure("best", foreground=good, font=("Segoe UI", 10, "bold"))

    def apply_app_theme(self, ctx):
        changed = ctx.get("mode", "light") != self._mode
        self._mode = ctx.get("mode", "light")
        app_theme.apply_editor_style(self.output, ctx)
        p = self._p()
        try:
            self.feat_list.configure(bg=p["entry_bg"], fg=p["entry_fg"], selectbackground=p["tree_sel_bg"],
                                     selectforeground=p["tree_sel_fg"], highlightbackground=p["border"])
            self._settings_canvas.configure(bg=p["bg_main"])
        except tk.TclError:
            pass
        self._configure_tags()
        if changed:
            self._draw_data_plot()
            self._draw_board_plot()
            self._draw_diag()

    def get_session_state(self):
        return {
            "format": "ml_studio",
            "file": self.file_var.get(),
            "target": self.target_var.get(),
            "features": [self.feat_list.get(i) for i in self.feat_list.curselection()],
            "vars": {k: getattr(self, k).get() for k in (
                "transform_var", "nan_var", "selector_var", "n_features", "scaler_var",
                "cv_folds", "test_size", "seed_var", "tune_var", "test_mode")},
            "models": {k: v.get() for k, v in self.model_vars.items()},
        }

    def apply_session_state(self, state):
        if not isinstance(state, dict) or state.get("format") != "ml_studio":
            raise ValueError("not an ML session")
        for k, v in (state.get("vars") or {}).items():
            try:
                getattr(self, k).set(v)
            except Exception:
                pass
        for k, v in (state.get("models") or {}).items():
            if k in self.model_vars and not (k == "XGBoost" and not _XGB_OK):
                self.model_vars[k].set(bool(v))
        path = state.get("file")
        if path and os.path.exists(path):
            self.load_csv(path, quiet=True)
            if state.get("target") in (self.target_cb["values"] or ()):
                self.target_var.set(state["target"])
                self._on_target_changed()
            feats = set(state.get("features") or [])
            if feats:
                self.feat_list.selection_clear(0, tk.END)
                for i in range(self.feat_list.size()):
                    if self.feat_list.get(i) in feats:
                        self.feat_list.selection_set(i)
                self._update_feat_count()

    # ============================================================
    # Data loading
    # ============================================================
    def log(self, text):
        self.output.insert(tk.END, text + "\n")
        self.output.see(tk.END)

    def browse(self):
        path = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv"), ("Excel", "*.xlsx *.xls"), ("All", "*.*")])
        if path:
            self.load_csv(path)

    def load_csv(self, path, quiet=False):
        try:
            if path.lower().endswith((".xlsx", ".xls")):
                df = pd.read_excel(path)
            else:
                df = pd.read_csv(path)
        except Exception as e:
            messagebox.showerror("Error", f"Could not read file:\n{e}")
            return
        df.columns = [str(c).strip() for c in df.columns]
        self.df = df
        self.file_var.set(path)
        numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
        non_numeric = [c for c in df.columns if c not in numeric]
        self._numeric_cols = numeric
        self.target_cb["values"] = numeric
        guess = next((c for c in numeric if "pred" in c.lower()), None) \
            or next((c for c in numeric if c.lower() in ("y", "target", "label")), None) \
            or (numeric[-1] if numeric else "")
        self.target_var.set(guess)
        n_nan = int(df[numeric].isna().sum().sum()) if numeric else 0
        self.data_info_var.set(f"{os.path.basename(path)} · {len(df)} rows · {len(df.columns)} columns "
                               f"({len(numeric)} numeric) · {n_nan} missing values"
                               + (f" · ignored text columns: {', '.join(non_numeric[:5])}"
                                  + ("…" if len(non_numeric) > 5 else "") if non_numeric else ""))
        self._on_target_changed()
        self._fill_preview(df)
        self.status_var.set(f"Loaded {os.path.basename(path)}")
        if not quiet:
            self.nb.select(self.tab_data)
            self.log(f"Loaded {path}: {df.shape[0]} rows × {df.shape[1]} columns; target guess = '{guess}'.")

    def _on_target_changed(self):
        target = self.target_var.get()
        feats = [c for c in getattr(self, "_numeric_cols", []) if c != target]
        prev = {self.feat_list.get(i) for i in self.feat_list.curselection()}
        self.feat_list.delete(0, tk.END)
        for c in feats:
            self.feat_list.insert(tk.END, c)
        for i, c in enumerate(feats):
            if not prev or c in prev:
                self.feat_list.selection_set(i)
        self.scatter_cb["values"] = feats
        if feats and self.scatter_feat.get() not in feats:
            self.scatter_feat.set(feats[0])
        self._update_feat_count()
        self._draw_data_plot()

    def _update_feat_count(self):
        self.feat_count_var.set(f"{len(self.feat_list.curselection())} of {self.feat_list.size()} selected")

    def _fill_preview(self, df):
        for w in self.preview_holder.winfo_children():
            w.destroy()
        cols = list(df.columns)
        tf, tree = self._tree(self.preview_holder, cols, [max(80, min(160, 9 * len(c))) for c in cols], height=8)
        for c in cols:
            tree.column(c, anchor="e" if pd.api.types.is_numeric_dtype(df[c]) else "w")
        tf.pack(fill=tk.BOTH, expand=True)
        for _, row in df.head(200).iterrows():
            tree.insert("", tk.END, values=["" if pd.isna(v) else (f"{v:.6g}" if isinstance(v, float) else v)
                                            for v in row.tolist()])
        self.preview_tree = tree

    def _xy(self, need_features=True):
        """Clean X (DataFrame) and y (Series) from the current selections."""
        if self.df is None:
            raise ValueError("Load a CSV file first.")
        target = self.target_var.get()
        if not target:
            raise ValueError("Choose a target column.")
        feats = [self.feat_list.get(i) for i in self.feat_list.curselection()]
        if need_features and not feats:
            raise ValueError("Select at least one feature column.")
        df = self.df[feats + [target]].copy()
        df = df[df[target].notna()]
        if self.nan_var.get() == "Drop rows":
            df = df.dropna()
        else:
            df[feats] = df[feats].fillna(df[feats].median())
        f, _inv, _u = TARGET_TRANSFORMS[self.transform_var.get()]
        y = pd.Series(f(df[target].astype(float).values), index=df.index, name=target)
        X = df[feats].astype(float)
        keep = np.isfinite(y.values)
        X = X.loc[keep]
        y = y.loc[keep]
        const = [c for c in X.columns if X[c].nunique() <= 1]
        if const:
            X = X.drop(columns=const)
        return X, y, const

    # ============================================================
    # Feature ranking preview
    # ============================================================
    def preview_ranking(self):
        if _ML_IMPORT_ERROR is not None:
            self._offer_install()
            return
        try:
            X, y, _const = self._xy()
        except Exception as e:
            messagebox.showwarning("ML", str(e))
            return
        method = self.selector_var.get()
        if method.startswith("None"):
            method = "SIS (|Pearson r|)"
        scores = feature_scores(X, y, method, int(self.seed_var.get())).sort_values(ascending=False)
        n = int(self.n_features.get())
        self.log(f"\nFeature ranking ({method}) on all rows:")
        for i, (c, s) in enumerate(scores.items()):
            self.log(f"  {i + 1:>3}. {c:<30} {s:.4f}{'  ✓' if i < n else ''}")
        self._last_ranking = (method, scores, n)
        self.data_plot_kind.set("Correlation with target")
        self._draw_data_plot(ranking=(method, scores, n))
        self.nb.select(self.tab_data)

    # ============================================================
    # Training
    # ============================================================
    def _offer_install(self):
        response = messagebox.askyesno(
            "Missing dependencies",
            "ML Studio requires 'scikit-learn' (and optionally 'xgboost').\n"
            f"Import error: {_ML_IMPORT_ERROR}\n\nInstall them now? (requires internet)"
        )
        if response:
            self.log("Installing dependencies... Please wait.")
            self.main_frame.update()
            try:
                import subprocess
                import sys
                subprocess.check_call([sys.executable, "-m", "pip", "install", "scikit-learn", "xgboost"])
                messagebox.showinfo("Success", "Installation complete!\nPlease restart AutoChemy to use the ML module.")
            except Exception as e:
                messagebox.showerror("Error", f"Installation failed:\n{e}")

    def run(self):
        if _ML_IMPORT_ERROR is not None:
            self._offer_install()
            return
        if self._worker and self._worker.is_alive():
            messagebox.showinfo("ML", "Training is already running.")
            return
        try:
            X, y, const = self._xy()
            models = [n for n, v in self.model_vars.items() if v.get()]
            if not models:
                raise ValueError("Select at least one model.")
            folds = int(self.cv_folds.get())
            test = float(self.test_size.get())
            if not 0 <= test < 0.9:
                raise ValueError("Test fraction must be between 0 and 0.9.")
            n_train = int(len(y) * (1 - test))
            if folds < 2 or folds > n_train:
                raise ValueError(f"CV folds must be between 2 and the number of training rows ({n_train}).")
            cfg = {
                "X": X, "y": y, "models": models, "folds": folds, "test": test,
                "seed": int(self.seed_var.get()), "selector": self.selector_var.get(),
                "n_feat": int(self.n_features.get()), "scaler": self.scaler_var.get(),
                "tune": bool(self.tune_var.get()), "test_mode": self.test_mode.get(),
                "transform": self.transform_var.get(), "target": self.target_var.get(),
                "file": self.file_var.get(),
            }
        except Exception as e:
            messagebox.showwarning("ML", str(e))
            return
        self.output.delete("1.0", tk.END)
        if const:
            self.log(f"Dropped constant columns: {', '.join(const)}")
        self.log(f"Training {len(cfg['models'])} model(s) on {len(y)} samples × {X.shape[1]} candidate features "
                 f"· {cfg['folds']}-fold CV · test fraction {cfg['test']} · seed {cfg['seed']}")
        self.results = {}
        self.progress.configure(maximum=len(cfg["models"]), value=0)
        self._stop_flag.clear()
        self.run_btn.config(state=tk.DISABLED, text="Training…")
        self.status_var.set("Training…")
        self.nb.select(self.tab_log)
        self._worker = threading.Thread(target=self.backend, args=(cfg,), daemon=True)
        self._worker.start()
        self.main_frame.after(100, self._poll)

    def _request_stop(self):
        if self._worker and self._worker.is_alive():
            self._stop_flag.set()
            self.log("Stop requested — finishing the current model…")

    def _poll(self):
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "log":
                    self.log(payload)
                elif kind == "progress":
                    self.progress.configure(value=payload[0])
                    self.status_var.set(f"Training {payload[1]} ({payload[0] + 1}/{payload[2]})…")
                elif kind == "result":
                    name, res = payload
                    self.results[name] = res
                    self._update_board()
                elif kind == "done":
                    self._on_done(payload)
                    return
                elif kind == "error":
                    self.run_btn.config(state=tk.NORMAL, text="▶  Train & Evaluate")
                    self.status_var.set("Error")
                    messagebox.showerror("ML error", payload)
                    return
        except queue.Empty:
            pass
        self.main_frame.after(120, self._poll)

    def backend(self, cfg):
        """Worker thread: never touches Tk widgets, reports through the queue."""
        q = self._queue
        warnings.filterwarnings("ignore", message=".*(converge|ConvergenceWarning|lbfgs|bound).*")
        try:
            X, y, seed = cfg["X"], cfg["y"], cfg["seed"]
            if cfg["test"] > 0:
                Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=cfg["test"], random_state=seed)
            else:
                Xtr, ytr, Xte, yte = X, y, X.iloc[:0], y.iloc[:0]

            feats = list(Xtr.columns)
            if not cfg["selector"].startswith("None"):
                scores = feature_scores(Xtr, ytr, cfg["selector"], seed).sort_values(ascending=False)
                feats = list(scores.head(min(cfg["n_feat"], len(scores))).index)
                q.put(("log", f"Selected {len(feats)} feature(s) by {cfg['selector']} on the training split:"))
                for c in feats:
                    q.put(("log", f"   {c:<30} score {scores[c]:.4f}"))
            Xtr, Xte = Xtr[feats], Xte[feats]
            q.put(("log", f"Train {len(ytr)} rows · test {len(yte)} rows\n"))

            zoo = _model_zoo(seed)
            kf = KFold(n_splits=cfg["folds"], shuffle=True, random_state=seed)
            avg = cfg["test_mode"].startswith("Average")
            for k, name in enumerate(cfg["models"]):
                if self._stop_flag.is_set():
                    q.put(("log", "Stopped."))
                    break
                if name not in zoo:
                    q.put(("log", f"⚠ {name}: not available, skipped"))
                    continue
                q.put(("progress", (k, name, len(cfg["models"]))))
                t0 = time.time()
                est, grid = zoo[name]
                pipe = Pipeline([("scaler", _make_scaler(cfg["scaler"])), ("model", est)])
                params = {}
                if cfg["tune"] and grid:
                    gs = GridSearchCV(pipe, grid, cv=KFold(min(5, cfg["folds"]), shuffle=True, random_state=seed),
                                      scoring="neg_root_mean_squared_error", n_jobs=-1)
                    gs.fit(Xtr.values, ytr.values)
                    params = {k2.replace("model__", ""): v for k2, v in gs.best_params_.items()}
                    pipe.set_params(**gs.best_params_)
                try:
                    oof_pred = np.zeros(len(ytr))
                    fold_models = []
                    for tr, val in kf.split(Xtr):
                        m = clone(pipe)
                        m.fit(Xtr.values[tr], ytr.values[tr])
                        oof_pred[val] = m.predict(Xtr.values[val])
                        fold_models.append(m)
                    final = clone(pipe).fit(Xtr.values, ytr.values)
                    res = {
                        "name": name, "features": feats, "params": params,
                        "oof_true": ytr.values, "oof_pred": oof_pred,
                        "cv": _metrics(ytr.values, oof_pred),
                        "final_model": final, "fold_models": fold_models,
                        "X_train": Xtr, "y_train": ytr,
                        "X_test": Xte, "y_test": yte,
                    }
                    if len(yte):
                        if avg:
                            yp = np.mean([m.predict(Xte.values) for m in fold_models], axis=0)
                        else:
                            yp = final.predict(Xte.values)
                        res["test_pred"] = yp
                        res["test"] = _metrics(yte.values, yp)
                    res["time"] = time.time() - t0
                except Exception as e:
                    q.put(("log", f"⚠ {name} failed: {e}"))
                    continue

                msg = f"🔹 {name:<22} CV RMSE {res['cv']['rmse']:.4f}  R² {res['cv']['r2']:.3f}"
                if "test" in res:
                    msg += f"  |  Test RMSE {res['test']['rmse']:.4f}  R² {res['test']['r2']:.3f}"
                msg += f"   ({res['time']:.1f}s)"
                if params:
                    msg += f"\n      tuned: {params}"
                q.put(("log", msg))
                q.put(("result", (name, res)))
            q.put(("done", cfg))
        except Exception:
            q.put(("error", traceback.format_exc()))

    def _on_done(self, cfg):
        self.run_btn.config(state=tk.NORMAL, text="▶  Train & Evaluate")
        self.progress.configure(value=self.progress["maximum"])
        self.run_config = {k: v for k, v in cfg.items() if k not in ("X", "y")}
        names = list(self.results)
        self.diag_model_cb["values"] = names
        self.pred_model_cb["values"] = names + (["(loaded model)"] if self.loaded_model else [])
        if names:
            best = self._ranked()[0]
            self.diag_model.set(best)
            self.pred_model.set(best)
            self.status_var.set(f"Done · best model: {best}")
            self.log(f"\nBest model by CV RMSE: {best}")
            self._update_board()
            self._draw_diag()
            self.nb.select(self.tab_board)
        else:
            self.status_var.set("No models finished")

    # ============================================================
    # Leaderboard & plots
    # ============================================================
    def _ranked(self):
        return sorted(self.results, key=lambda n: self.results[n]["cv"]["rmse"])

    def _update_board(self):
        self.board_tree.delete(*self.board_tree.get_children())
        for i, name in enumerate(self._ranked()):
            r = self.results[name]
            t = r.get("test", {})
            vals = (i + 1, name, f"{r['cv']['rmse']:.4f}", f"{r['cv']['mae']:.4f}", f"{r['cv']['r2']:.3f}",
                    f"{t['rmse']:.4f}" if t else "—", f"{t['mae']:.4f}" if t else "—",
                    f"{t['r2']:.3f}" if t else "—", f"{(t or r['cv'])['maxerr']:.4f}", f"{r['time']:.1f}")
            self.board_tree.insert("", tk.END, iid=name, values=vals, tags=("best",) if i == 0 else ())
        unit = TARGET_TRANSFORMS[self.transform_var.get()][2]
        if self.results:
            self.board_info_var.set(f"Ranked by cross-validated RMSE{f' (units: {unit})' if unit else ''}. "
                                    "CV metrics use out-of-fold predictions on the training split; test metrics use "
                                    "the held-out split. Double-click a row for diagnostics.")
        self._draw_board_plot()

    def _board_open_diag(self, _e=None):
        sel = self.board_tree.selection()
        if sel:
            self.diag_model.set(sel[0])
            self.nb.select(self.tab_diag)
            self._draw_diag()

    def _draw_data_plot(self, ranking=None):
        P = getattr(self, "data_plot", None)
        if not P:
            return
        p = self._p()
        if self.df is None:
            P.empty(p, "No data loaded")
            return
        try:
            X, y, _ = self._xy(need_features=False)
        except Exception as e:
            P.empty(p, str(e))
            return
        kind = self.data_plot_kind.get()
        P.begin(p)
        ax = P.fig.add_subplot(111)
        P.style(ax, p)
        if kind == "Target distribution":
            ax.hist(y.values, bins=min(40, max(8, len(y) // 5)), color=p["accent"], alpha=0.8, edgecolor=p["tree_bg"])
            ax.set_xlabel(y.name)
            ax.set_ylabel("Count")
            ax.set_title(f"Target distribution · mean {y.mean():.3f} · std {y.std():.3f}", fontsize=10)
        elif kind == "Feature vs target":
            feat = self.scatter_feat.get()
            if feat in self.df.columns:
                xs = self.df.loc[y.index, feat]
                ax.scatter(xs, y.values, s=18, alpha=0.7, color=p["accent"])
                r = np.corrcoef(xs.fillna(xs.median()), y.values)[0, 1] if len(y) > 2 else float("nan")
                ax.set_xlabel(feat)
                ax.set_ylabel(y.name)
                ax.set_title(f"{feat} vs {y.name} · r = {r:.3f}", fontsize=10)
        elif X.shape[1] == 0:
            ax.text(0.5, 0.5, "Select feature columns", ha="center", transform=ax.transAxes, color=p["fg_muted"])
        elif kind == "Correlation heatmap":
            cols = list(X.corrwith(y).abs().sort_values(ascending=False).head(25).index)
            C = pd.concat([X[cols], y], axis=1).corr().values
            im = ax.imshow(C, cmap="RdBu_r", vmin=-1, vmax=1)
            labels = cols + [y.name]
            ax.set_xticks(range(len(labels)))
            ax.set_yticks(range(len(labels)))
            ax.set_xticklabels(labels, rotation=90, fontsize=7)
            ax.set_yticklabels(labels, fontsize=7)
            ax.grid(False)
            cb = P.fig.colorbar(im, ax=ax, fraction=0.04)
            cb.ax.tick_params(colors=p["fg"], labelsize=8)
            ax.set_title("Pearson correlation (top 25 features by |r| with target)", fontsize=10)
        else:
            if ranking:
                method, scores, n = ranking
            else:
                method, n = "Pearson r", int(self.n_features.get() or 5)
                scores = X.corrwith(y).fillna(0)
            s = scores.reindex(scores.abs().sort_values(ascending=False).index).head(30)
            colors = [("#16a34a" if i < n else p["fg_muted"]) if ranking else ("#2563eb" if v >= 0 else "#dc2626")
                      for i, v in enumerate(s.values)]
            ax.barh(range(len(s))[::-1], s.values, color=colors)
            ax.set_yticks(range(len(s))[::-1])
            ax.set_yticklabels(s.index, fontsize=8)
            ax.axvline(0, color=p["fg_muted"], lw=0.8)
            ax.set_title(f"{method} with {y.name}" + (f" · green = top {n} kept" if ranking else ""), fontsize=10)
        P.finish()

    def _draw_board_plot(self):
        P = getattr(self, "board_plot", None)
        if not P:
            return
        p = self._p()
        if not self.results:
            P.empty(p, "No trained models yet")
            return
        metric = {"RMSE": "rmse", "MAE": "mae", "R²": "r2"}[self.board_metric.get()]
        names = self._ranked()
        cv = [self.results[n]["cv"][metric] for n in names]
        te = [self.results[n].get("test", {}).get(metric, np.nan) for n in names]
        P.begin(p)
        ax = P.fig.add_subplot(111)
        P.style(ax, p)
        idx = np.arange(len(names))
        ax.bar(idx - 0.2, cv, 0.4, label=f"CV {self.board_metric.get()}", color=p["accent"])
        if not all(np.isnan(te)):
            ax.bar(idx + 0.2, te, 0.4, label=f"Test {self.board_metric.get()}", color="#f97316")
        ax.set_xticks(idx)
        ax.set_xticklabels(names, rotation=25, ha="right", fontsize=8)
        ax.set_ylabel(self.board_metric.get())
        P.legend(ax, p)
        P.finish()

    def _draw_diag(self):
        P = getattr(self, "diag_plot", None)
        if not P:
            return
        p = self._p()
        name = self.diag_model.get()
        kind = self.diag_kind.get()
        if not self.results or (name not in self.results and kind != "All models parity"):
            P.empty(p, "Train models to see diagnostics")
            self.diag_info_var.set("")
            return
        unit = TARGET_TRANSFORMS[self.transform_var.get()][2]
        ul = f" ({unit})" if unit else ""
        P.begin(p)
        if kind == "All models parity":
            names = self._ranked()
            cols = min(3, len(names))
            rows = int(np.ceil(len(names) / cols))
            for i, n in enumerate(names):
                ax = P.fig.add_subplot(rows, cols, i + 1)
                P.style(ax, p)
                self._parity(ax, self.results[n], p, small=True)
                ax.set_title(n, fontsize=8)
            self.diag_info_var.set("Out-of-fold (circles) and test (crosses) predictions for every model")
            P.finish()
            return
        r = self.results[name]
        ax = P.fig.add_subplot(111)
        P.style(ax, p)
        if kind == "Parity plot":
            self._parity(ax, r, p)
            ax.set_xlabel(f"Actual{ul}")
            ax.set_ylabel(f"Predicted{ul}")
            ax.set_title(f"{name} — parity", fontsize=10)
            P.legend(ax, p, loc="upper left")
        elif kind in ("Residuals vs predicted", "Residual histogram"):
            res_cv = r["oof_true"] - r["oof_pred"]
            res_te = (r["y_test"].values - r["test_pred"]) if "test_pred" in r else None
            if kind.startswith("Residuals vs"):
                ax.scatter(r["oof_pred"], res_cv, s=16, alpha=0.6, color=p["accent"], label="CV (out-of-fold)")
                if res_te is not None:
                    ax.scatter(r["test_pred"], res_te, s=28, marker="x", color="#f97316", label="Test")
                ax.axhline(0, color=p["fg_muted"], ls="--")
                ax.set_xlabel(f"Predicted{ul}")
                ax.set_ylabel(f"Residual (actual − predicted){ul}")
            else:
                ax.hist(res_cv, bins=min(40, max(8, len(res_cv) // 4)), color=p["accent"], alpha=0.75, label="CV")
                if res_te is not None:
                    ax.hist(res_te, bins=min(20, max(5, len(res_te) // 2)), color="#f97316", alpha=0.65, label="Test")
                ax.set_xlabel(f"Residual{ul}")
                ax.set_ylabel("Count")
            ax.set_title(f"{name} — residuals", fontsize=10)
            P.legend(ax, p)
        elif kind.startswith("Feature importance"):
            Xe, ye = (r["X_test"], r["y_test"]) if len(r["y_test"]) >= 5 else (r["X_train"], r["y_train"])
            pi = permutation_importance(r["final_model"], Xe.values, ye.values, n_repeats=15,
                                        random_state=int(self.seed_var.get()), scoring="neg_root_mean_squared_error")
            order = np.argsort(pi.importances_mean)
            feats = np.array(r["features"])[order][-25:]
            ax.barh(range(len(feats)), pi.importances_mean[order][-25:], xerr=pi.importances_std[order][-25:],
                    color=p["accent"], ecolor=p["fg_muted"])
            ax.set_yticks(range(len(feats)))
            ax.set_yticklabels(feats, fontsize=8)
            ax.set_xlabel(f"Increase in RMSE when shuffled{ul}")
            ax.set_title(f"{name} — permutation importance ({'test' if Xe is r['X_test'] else 'train'} set)", fontsize=10)
        elif kind == "Learning curve":
            self.main_frame.config(cursor="watch")
            self.main_frame.update_idletasks()
            try:
                Xt, yt = r["X_train"].values, r["y_train"].values
                cv = KFold(min(5, int(self.cv_folds.get())), shuffle=True, random_state=int(self.seed_var.get()))
                sizes, tr, va = learning_curve(clone(r["final_model"]), Xt, yt, cv=cv,
                                               train_sizes=np.linspace(0.2, 1.0, 6),
                                               scoring="neg_root_mean_squared_error", n_jobs=-1)
            finally:
                self.main_frame.config(cursor="")
            for scores, lbl, col in ((-tr, "Training", p["accent"]), (-va, "Validation", "#f97316")):
                m, s = scores.mean(1), scores.std(1)
                ax.plot(sizes, m, "-o", color=col, label=lbl, ms=4)
                ax.fill_between(sizes, m - s, m + s, color=col, alpha=0.15)
            ax.set_xlabel("Training samples")
            ax.set_ylabel(f"RMSE{ul}")
            ax.set_title(f"{name} — learning curve", fontsize=10)
            P.legend(ax, p)
        t = r.get("test")
        info = f"CV RMSE {r['cv']['rmse']:.4f} · R² {r['cv']['r2']:.3f}"
        if t:
            info += f"   |   Test RMSE {t['rmse']:.4f} · R² {t['r2']:.3f}"
        if r.get("params"):
            info += f"   |   {r['params']}"
        self.diag_info_var.set(info)
        P.finish()

    def _parity(self, ax, r, p, small=False):
        ax.scatter(r["oof_true"], r["oof_pred"], s=10 if small else 18, alpha=0.6, color=p["accent"],
                   label=f"CV  R²={r['cv']['r2']:.3f}")
        allv = [r["oof_true"], r["oof_pred"]]
        if "test_pred" in r:
            ax.scatter(r["y_test"].values, r["test_pred"], s=18 if small else 36, marker="x", color="#f97316",
                       label=f"Test R²={r['test']['r2']:.3f}")
            allv += [r["y_test"].values, r["test_pred"]]
        lo = min(np.min(a) for a in allv)
        hi = max(np.max(a) for a in allv)
        pad = (hi - lo) * 0.05 or 1
        ax.plot([lo - pad, hi + pad], [lo - pad, hi + pad], "--", color=p["fg_muted"], lw=1)
        ax.set_xlim(lo - pad, hi + pad)
        ax.set_ylim(lo - pad, hi + pad)
        if small:
            ax.tick_params(labelsize=7)

    # ============================================================
    # Predict / save / load / export
    # ============================================================
    def _model_bundle(self, name):
        if name == "(loaded model)":
            return self.loaded_model
        r = self.results.get(name)
        if not r:
            return None
        cfg = self.run_config or {}
        return {"model": r["final_model"], "features": r["features"], "name": name,
                "target": cfg.get("target", self.target_var.get()), "transform": cfg.get("transform", "None"),
                "cv": r["cv"], "test": r.get("test"), "params": r.get("params", {}),
                "trained_on": cfg.get("file"), "created": time.strftime("%Y-%m-%d %H:%M")}

    def save_model(self):
        if _ML_IMPORT_ERROR is not None:
            self._offer_install()
            return
        name = self.diag_model.get() or (self._ranked()[0] if self.results else "")
        bundle = self._model_bundle(name)
        if not bundle:
            messagebox.showinfo("ML", "Train a model first.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".joblib", filetypes=[("Model", "*.joblib")],
                                            initialfile=f"{name.replace(' ', '_')}.joblib")
        if path:
            joblib.dump(bundle, path)
            self.log(f"Saved {name} to {path}")
            self.status_var.set(f"Saved {os.path.basename(path)}")

    def load_model(self):
        if _ML_IMPORT_ERROR is not None:
            self._offer_install()
            return
        path = filedialog.askopenfilename(filetypes=[("Model", "*.joblib"), ("All", "*.*")])
        if not path:
            return
        if not messagebox.askyesno("Load model", "Model files can run code when loaded.\n"
                                                 "Only load files you created or trust. Continue?"):
            return
        try:
            bundle = joblib.load(path)
            if not isinstance(bundle, dict) or "model" not in bundle or "features" not in bundle:
                raise ValueError("Not an AutoChemy ML model file.")
        except Exception as e:
            messagebox.showerror("Error", f"Could not load model:\n{e}")
            return
        self.loaded_model = bundle
        self.pred_model_cb["values"] = list(self.results) + ["(loaded model)"]
        self.pred_model.set("(loaded model)")
        self.log(f"Loaded model '{bundle.get('name')}' (target {bundle.get('target')}, "
                 f"{len(bundle['features'])} features, created {bundle.get('created', '?')}).")
        self.nb.select(self.tab_pred)

    def predict_csv(self):
        bundle = self._model_bundle(self.pred_model.get())
        if not bundle:
            messagebox.showinfo("ML", "Train or load a model first.")
            return
        path = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv"), ("Excel", "*.xlsx *.xls")])
        if not path:
            return
        try:
            df = pd.read_excel(path) if path.lower().endswith((".xlsx", ".xls")) else pd.read_csv(path)
            df.columns = [str(c).strip() for c in df.columns]
            missing = [c for c in bundle["features"] if c not in df.columns]
            if missing:
                raise ValueError(f"Missing feature columns: {', '.join(missing)}")
            X = df[bundle["features"]].astype(float)
            X = X.fillna(X.median())
            yp = bundle["model"].predict(X.values)
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return
        out = df.copy()
        tgt = bundle.get("target") or "y"
        transform = bundle.get("transform", "None")
        out[f"pred_{tgt}{'_transformed' if transform != 'None' else ''}"] = yp
        if transform != "None":
            try:
                out[f"pred_{tgt}"] = TARGET_TRANSFORMS[transform][1](yp)
            except Exception:
                pass
        if tgt in df.columns and transform == "None":
            m = _metrics(df[tgt].values, yp)
            self.pred_info_var.set(f"{len(out)} predictions · file contains '{tgt}': RMSE {m['rmse']:.4f}, R² {m['r2']:.3f}")
        else:
            self.pred_info_var.set(f"{len(out)} predictions from {bundle.get('name')} on {os.path.basename(path)}")
        self.pred_df = out
        for w in self.pred_holder.winfo_children():
            w.destroy()
        cols = list(out.columns)
        tf, tree = self._tree(self.pred_holder, cols, [max(80, min(160, 9 * len(c))) for c in cols], height=14)
        tf.pack(fill=tk.BOTH, expand=True)
        for _, row in out.head(1000).iterrows():
            tree.insert("", tk.END, values=["" if pd.isna(v) else (f"{v:.6g}" if isinstance(v, float) else v)
                                            for v in row.tolist()])

    def export_predictions(self):
        if self.pred_df is None:
            messagebox.showinfo("ML", "Run a prediction first.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if path:
            self.pred_df.to_csv(path, index=False)
            self.status_var.set(f"Saved {os.path.basename(path)}")

    def export_results(self):
        if not self.results:
            messagebox.showinfo("ML", "Train models first.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")],
                                            initialfile="ml_leaderboard.csv")
        if not path:
            return
        rows = []
        for i, n in enumerate(self._ranked()):
            r = self.results[n]
            row = {"rank": i + 1, "model": n}
            row.update({f"cv_{k}": v for k, v in r["cv"].items()})
            row.update({f"test_{k}": v for k, v in (r.get("test") or {}).items()})
            row["features"] = ";".join(r["features"])
            row["params"] = str(r.get("params") or "")
            row["time_s"] = round(r["time"], 2)
            rows.append(row)
        pd.DataFrame(rows).to_csv(path, index=False)
        best = self.results[self._ranked()[0]]
        pred_path = os.path.splitext(path)[0] + "_predictions.csv"
        parts = [pd.DataFrame({"split": "cv", "actual": best["oof_true"], "predicted": best["oof_pred"]},
                              index=best["X_train"].index)]
        if "test_pred" in best:
            parts.append(pd.DataFrame({"split": "test", "actual": best["y_test"].values, "predicted": best["test_pred"]},
                                      index=best["X_test"].index))
        pd.concat(parts).rename_axis("row").to_csv(pred_path)
        self.log(f"Exported leaderboard to {path}\nExported best-model predictions to {pred_path}")
        self.status_var.set("Results exported")
