import os
import pickle
import pandas as pd
import modules.codes_dia.main as main

from modules.codes_dia.xyz_extractor import read_multi_xyz
from modules.codes_dia.orca_input_generator_for_xyz import write_xyz
import modules.codes_dia.geometric_parameters as gp
import modules.codes_dia.dis_int_plot as plot

# ================= XYZ =================
all_xyz = read_multi_xyz(main.inp_file, main.RMSD)

with open(os.path.join(main.output_dir, "all_xyz.pkl"), "wb") as f:
    pickle.dump(all_xyz, f)

# ================= FRAGMENTS =================
num_atoms = len(all_xyz[0])
total = list(range(1, num_atoms + 1))

frag1 = main.frag1_list
frag2 = [x for x in total if x not in frag1]

frag1 = [x - 1 for x in frag1]
frag2 = [x - 1 for x in frag2]

out_folder = os.path.join(main.output_dir, "DIA_output")
os.makedirs(out_folder, exist_ok=True)

for i, coord in enumerate(all_xyz):
    write_xyz(coord.loc[frag1], f"{out_folder}/frag1_{i}.xyz")
    write_xyz(coord.loc[frag2], f"{out_folder}/frag2_{i}.xyz")
    write_xyz(coord, f"{out_folder}/complete_{i}.xyz")

# ================= GEOMETRY =================
res = (
    gp.geom_along_frames(all_xyz, bond=main.BOND) if main.MODE == 'b'
    else gp.geom_along_frames(all_xyz, angle=main.ANGLE) if main.MODE == 'a'
    else gp.geom_along_frames(all_xyz, dihedral=main.DIHEDRAL)
)

# ================= SAVE =================
df = pd.DataFrame(res)
df.to_csv(os.path.join(out_folder, "geometry.csv"), index=False)

# ================= PLOT =================
plot.plot_dis_int_figure(df, main.MODE)