import numpy as np
import pandas as pd
import math
import importlib
import os
from typing import Tuple, List
import pickle
import codes.main 

print("hari")
#for extracting all xyz files from irc file 
if codes.main.extract_xyz_from_irc == 1:
    from codes.xyz_extractor import read_multi_xyz
    all_xyz = read_multi_xyz(f"files/{codes.main.inp_file}",codes.main.RMSD)
    with open("codes/all_xyz.pkl", "wb") as f:
        pickle.dump(all_xyz, f)
    print("done")
else:
    with open("codes/all_xyz.pkl", "rb") as f:
        all_xyz = pickle.load(f)
    print("using previous coordinates")


if codes.main.create_xyz_files == 1:
    from codes.orca_input_generator_for_xyz import write_xyz
    num_atoms = len(all_xyz[0])
    total_atom_list = list(range(1,num_atoms+1))
    frag1_list = codes.main.frag1_list
    frag2_list = total_atom_list
    for i in frag1_list:
        frag2_list.remove(i)

    frag1_list = [x - 1 for x in frag1_list]
    frag2_list = [x - 1 for x in frag2_list]

    os.makedirs(codes.main.inp_folder, exist_ok=True)
    i = 0
    for coord in all_xyz:
        temp_frag1 = coord.loc[frag1_list]
        temp_frag2 = coord.loc[frag2_list]
        write_xyz(temp_frag1,f"{codes.main.inp_folder}/frag1_{i}.xyz")
        write_xyz(temp_frag2,f"{codes.main.inp_folder}/frag2_{i}.xyz")
        write_xyz(coord,f"{codes.main.inp_folder}/complete_{i}.xyz")
        i = i+1

else:
    print("xyz_already_created")

#input folder , charge , spin
if codes.main.job_submit == 1:
    from orca_input_generator_for_xyz import input_creator
    input_creator("all_inp_files3/",0,1)
else:
    print("no jobs today")


if codes.main.geometric_parameter_extraction == 1:
    importlib.reload(codes.geometric_parameters)
    res = (
    codes.geometric_parameters.geom_along_frames(all_xyz, bond=codes.main.BOND)     if codes.main.MODE=='b' else
    codes.geometric_parameters.geom_along_frames(all_xyz, angle=codes.main.ANGLE)   if codes.main.MODE=='a' else
    codes.geometric_parameters.geom_along_frames(all_xyz, dihedral=codes.main.DIHEDRAL) if codes.main.MODE=='d' else
    None
    )
    with open("codes/res.pkl", "wb") as f:
        pickle.dump(res, f)

else:
    with open("codes/res.pkl", "rb") as f:
        res = pickle.load(f)
    print("using previous res")

print(codes.main.inp_folder)
if codes.main.extract_singlepoint_E == 1:
    from codes.orca_input_generator_for_xyz import read_out_files
    all_sp_data = read_out_files(codes.main.inp_folder)
    col1 = ["file_name","final_sp_value"]
    sp_df = pd.DataFrame(all_sp_data, columns=col1)
    sp_df.head()
    sp_df.to_csv("files/single_point_data.csv", index=False)

data = pd.read_csv("files/single_point_data.csv")
n = int((len(data))/3)

comp_sp_data = list(data[0:n]["final_sp_value"])
frag1_sp_data = list(data[n:2*n]["final_sp_value"])
frag2_sp_data = list(data[2*n:3*n]["final_sp_value"])

distortion_1 = [(x - codes.main.frag1_opt_e)*627.509 for x in frag1_sp_data]
distortion_2 = [(x - codes.main.frag2_opt_e)*627.509 for x in frag2_sp_data]
total_distortion = [d1 + d2 for d1, d2 in zip(distortion_1, distortion_2)]

interaction = [(comp_sp_data[i] - (frag1_sp_data[i] + frag2_sp_data[i])) * 627.509
               for i in range(len(comp_sp_data))]


if codes.main.plot_the_graph == 1:
    importlib.reload(codes.dis_int_plot)
    res = list(res["bond"].round(2))
    if codes.main.MODE=='b':
        selected_parameter = "bond_length"
    elif codes.main.MODE=='a':
        selected_pareameter = "angle"
    else:
        selected_parameter = "dihedral_angle"
    
    cols = [selected_parameter, "dis_1", "dis_2", "dis_total", "intr"]
    dis_int_df = pd.DataFrame(list(zip(res,distortion_1,distortion_2,total_distortion,interaction)), columns=cols)
    dis_int_df.drop_duplicates(subset = selected_parameter)
    codes.dis_int_plot.plot_dis_int_figure(dis_int_df,codes.main.MODE)