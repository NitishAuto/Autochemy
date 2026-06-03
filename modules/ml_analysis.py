import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

from modules.base_module import BaseModule

_ML_IMPORT_ERROR = None
try:
    from sklearn.model_selection import train_test_split, KFold
    from sklearn.preprocessing import MinMaxScaler, StandardScaler
    from sklearn.metrics import mean_squared_error, r2_score
    from sklearn.pipeline import Pipeline
    from sklearn.linear_model import LinearRegression
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.svm import SVR
    from xgboost import XGBRegressor
except Exception as exc:
    _ML_IMPORT_ERROR = exc


# ================= SIS FUNCTION =================
def sis(X, y, n_features):
    corr = X.corrwith(y).abs()
    top_features = corr.sort_values(ascending=False).head(n_features).index
    return X[top_features]


# ================= PLOT FUNCTION =================
def plot_ml_results(results_dict):

    n = len(results_dict)
    cols = 2
    rows = int(np.ceil(n / 2))

    fig, axes = plt.subplots(rows, cols, figsize=(12, 5 * rows))
    axes = axes.flatten()

    for i, (model_name, data) in enumerate(results_dict.items()):
        ax = axes[i]

        ax.scatter(data["y_train_true"], data["y_train_pred"],
                   label="Train", alpha=0.6, marker="o")

        ax.scatter(data["y_test_true"], data["y_test_pred"],
                   label="Test", alpha=0.8, marker="x")

        min_val = min(data["y_test_true"].min(), data["y_train_true"].min())
        max_val = max(data["y_test_true"].max(), data["y_train_true"].max())

        ax.plot([min_val, max_val], [min_val, max_val], 'k--')

        ax.set_title(model_name)
        ax.set_xlabel("Actual")
        ax.set_ylabel("Predicted")
        ax.legend()
        ax.grid(True, linestyle="--", alpha=0.4)

    plt.tight_layout()
    plt.show()


# ============================================================
class MLAnalysisModule(BaseModule):

    def get_name(self):
        return "ML"

    def get_icon(self):
        return "🤖"

    # ============================================================
    def create_ui(self):
        self.main_frame = ttk.Frame(self.parent_frame, padding=25)

        style = ttk.Style()
        style.theme_use("clam")

        style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"))

        # ===== HEADER =====
        head = ttk.Frame(self.main_frame)
        head.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(head, text=f"{self.get_icon()}  {self.get_name()}", font=("Segoe UI", 13, "bold"), foreground="#0b5cab").pack(side=tk.LEFT)
        ttk.Separator(self.main_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, 10))

        # ===== FILE INPUT =====
        file_frame = ttk.LabelFrame(self.main_frame, text="Input CSV", padding=10)
        file_frame.pack(fill=tk.X, pady=10)

        self.file_var = tk.StringVar()

        ttk.Entry(file_frame, textvariable=self.file_var, width=60)\
            .pack(side=tk.LEFT, padx=5)

        ttk.Button(file_frame, text="Browse",
                   command=self.browse).pack(side=tk.LEFT)

        # ===== SIS =====
        sis_frame = ttk.LabelFrame(self.main_frame, text="SIS Feature Selection", padding=10)
        sis_frame.pack(fill=tk.X, pady=10)

        self.use_sis = tk.BooleanVar(value=True)
        self.n_features = tk.IntVar(value=5)

        ttk.Checkbutton(sis_frame, text="Use SIS",
                        variable=self.use_sis).pack(side=tk.LEFT, padx=5)

        ttk.Label(sis_frame, text="Features").pack(side=tk.LEFT, padx=10)
        ttk.Entry(sis_frame, textvariable=self.n_features, width=5).pack(side=tk.LEFT)

        # ===== MODEL SELECTION =====
        model_frame = ttk.LabelFrame(self.main_frame, text="Models", padding=10)
        model_frame.pack(fill=tk.X, pady=10)

        self.use_lr = tk.BooleanVar(value=True)
        self.use_rf = tk.BooleanVar(value=True)
        self.use_svr = tk.BooleanVar(value=True)
        self.use_xgb = tk.BooleanVar(value=True)

        ttk.Checkbutton(model_frame, text="Linear Regression", variable=self.use_lr).grid(row=0, column=0, padx=10)
        ttk.Checkbutton(model_frame, text="Random Forest", variable=self.use_rf).grid(row=0, column=1, padx=10)
        ttk.Checkbutton(model_frame, text="SVR", variable=self.use_svr).grid(row=0, column=2, padx=10)
        ttk.Checkbutton(model_frame, text="XGBoost", variable=self.use_xgb).grid(row=0, column=3, padx=10)

        # ===== SETTINGS =====
        settings_frame = ttk.LabelFrame(self.main_frame, text="Training Settings", padding=10)
        settings_frame.pack(fill=tk.X, pady=10)

        self.cv_folds = tk.IntVar(value=10)
        self.test_size = tk.DoubleVar(value=0.1)

        ttk.Label(settings_frame, text="CV Folds").grid(row=0, column=0)
        ttk.Entry(settings_frame, textvariable=self.cv_folds, width=5).grid(row=0, column=1)

        ttk.Label(settings_frame, text="Test Size").grid(row=0, column=2, padx=15)
        ttk.Entry(settings_frame, textvariable=self.test_size, width=5).grid(row=0, column=3)

        # ===== RUN =====
        ttk.Button(self.main_frame, text="🚀 Run ML",
                   command=self.run).pack(pady=10)

        # ===== OUTPUT =====
        self.output = tk.Text(self.main_frame, height=18)
        self.output.pack(fill=tk.BOTH, expand=True)

    # ============================================================
    def browse(self):
        path = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv")])
        if path:
            self.file_var.set(path)

    # ============================================================
    def run(self):
        if _ML_IMPORT_ERROR is not None:
            messagebox.showerror(
                "Missing dependency",
                "ML Analysis dependencies are missing.\n"
                "Install required packages:\n"
                "pip install scikit-learn xgboost\n\n"
                f"Details: {_ML_IMPORT_ERROR}"
            )
            return
        threading.Thread(target=self.backend).start()

    # ============================================================
    def backend(self):
        try:
            self.output.delete("1.0", tk.END)

            df = pd.read_csv(self.file_var.get())

            target = [c for c in df.columns if "pred" in c.lower()][0]

            X = df.drop(columns=[target])
            y = df[target]

            X = X.values
            Y = y.values.ravel() / 23.06

            # ===== SIS =====
            if self.use_sis.get():
                X_df = pd.DataFrame(X)
                X_df = sis(X_df, pd.Series(Y), self.n_features.get())
                X = X_df.values

            # ===== SPLIT =====
            X, xt, Y, yt = train_test_split(
                X, Y, test_size=self.test_size.get(), random_state=108
            )

            scaler = MinMaxScaler()
            X = scaler.fit_transform(X)
            xt = scaler.transform(xt)

            # ===== MODELS =====
            models_dict = {}

            if self.use_lr.get():
                models_dict["Linear Regression"] = LinearRegression()

            if self.use_rf.get():
                models_dict["Random Forest"] = RandomForestRegressor()

            if self.use_svr.get():
                models_dict["SVR"] = SVR()

            if self.use_xgb.get():
                models_dict["XGBoost"] = XGBRegressor()

            results_text = ""
            plot_data = {}

            kf = KFold(n_splits=self.cv_folds.get(), shuffle=True, random_state=108)

            for name, base_model in models_dict.items():

                preds, acts, models = [], [], []

                for tr, val in kf.split(X):

                    Xtr, Xval = X[tr], X[val]
                    ytr, yval = Y[tr], Y[val]

                    # FIX LR scaling issue
                    if name == "Linear Regression":
                        model = base_model
                    else:
                        model = Pipeline([
                            ("scaler", StandardScaler()),
                            ("model", base_model)
                        ])

                    model.fit(Xtr, ytr)
                    yp = model.predict(Xval)

                    preds.append(yp)
                    acts.append(yval)
                    models.append(model)

                ytr_true = np.concatenate(acts)
                ytr_pred = np.concatenate(preds)

                rmse_tr = np.sqrt(mean_squared_error(ytr_true, ytr_pred))
                r2_tr = r2_score(ytr_true, ytr_pred)

                yt_pred = np.mean([m.predict(xt) for m in models], axis=0)

                rmse_te = np.sqrt(mean_squared_error(yt, yt_pred))
                r2_te = r2_score(yt, yt_pred)

                plot_data[name] = {
                    "y_train_true": ytr_true,
                    "y_train_pred": ytr_pred,
                    "y_test_true": yt,
                    "y_test_pred": yt_pred
                }

                results_text += f"\n🔹 {name}\n"
                results_text += f"Train RMSE: {rmse_tr:.4f}\n"
                results_text += f"Train R2  : {r2_tr:.4f}\n"
                results_text += f"Test RMSE : {rmse_te:.4f}\n"
                results_text += f"Test R2   : {r2_te:.4f}\n"

            self.output.insert(tk.END, results_text)

            plot_ml_results(plot_data)

        except Exception as e:
            messagebox.showerror("Error", str(e))