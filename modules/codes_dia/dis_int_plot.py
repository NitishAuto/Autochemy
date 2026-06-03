import matplotlib.pyplot as plt

def plot_dis_int_figure(dis_int_df, MODE):

    # ===== DEFAULT STYLE =====
    FIG_W, FIG_H = 10, 6

    colors = {
        "dis1": "#1f77b4",
        "dis2": "#ff7f0e",
        "total": "#2ca02c",
        "intr": "#d62728"
    }

    x = dis_int_df.iloc[:, 0]
    y1 = dis_int_df["dis_1"]
    y2 = dis_int_df["dis_2"]
    y3 = dis_int_df["dis_total"]
    y4 = dis_int_df["intr"]

    fig, ax = plt.subplots(figsize=(FIG_W, FIG_H))

    # ===== DRAW FUNCTION =====
    def draw():
        ax.clear()

        ax.plot(x, y1, label="Distortion 1", color=colors["dis1"], lw=2)
        ax.plot(x, y2, label="Distortion 2", color=colors["dis2"], lw=2)
        ax.plot(x, y3, label="Total Distortion", color=colors["total"], lw=2)
        ax.plot(x, y4, label="Interaction", color=colors["intr"], lw=2)

        if MODE == 'b':
            ax.set_xlabel("Bond length (Å)")
        elif MODE == 'a':
            ax.set_xlabel("Angle (deg)")
        else:
            ax.set_xlabel("Dihedral Angle (deg)")

        ax.set_ylabel("Energy (kcal/mol)")
        ax.set_title("Distortion / Interaction Analysis", fontsize=14, weight='bold')

        ax.grid(True, linestyle="--", alpha=0.5)
        ax.legend(loc="best")

        ax.invert_xaxis()

        fig.canvas.draw_idle()

    # ===== KEYBOARD CONTROLS =====
    def on_key(event):
        # 🎨 Change color presets quickly
        if event.key == '1':
            colors["dis1"] = "red"
        elif event.key == '2':
            colors["dis2"] = "blue"
        elif event.key == '3':
            colors["total"] = "green"
        elif event.key == '4':
            colors["intr"] = "black"

        # 🎨 Switch theme
        elif event.key == 't':
            colors.update({
                "dis1": "#e41a1c",
                "dis2": "#377eb8",
                "total": "#4daf4a",
                "intr": "#984ea3"
            })

        # 🔄 Toggle X reverse
        elif event.key == 'r':
            ax.invert_xaxis()

        draw()

    fig.canvas.mpl_connect('key_press_event', on_key)

    # ===== INITIAL DRAW =====
    draw()

    plt.tight_layout()
    plt.show()