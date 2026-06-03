import numpy as np
import pandas as pd

def bond_length(r1,r2): return np.linalg.norm(r2-r1)
    
def angle_deg(r1,r2,r3):
    v1,v2=r1-r2,r3-r2
    return float(np.degrees(np.arccos(np.clip(np.dot(v1,v2)/(np.linalg.norm(v1)*np.linalg.norm(v2)),-1,1))))

def dihedral_deg(r1,r2,r3,r4):
    b0,b1,b2=r2-r1,r3-r2,r4-r3
    b1n=b1/np.linalg.norm(b1)
    v=np.cross(b0,b1); w=np.cross(b1,b2)
    x=np.dot(v,w); y=np.dot(np.cross(v,b1n),w)
    return float(np.degrees(np.arctan2(y,x)))

def geom_along_frames(all_xyz,bond=None,angle=None,dihedral=None):
    rows=[]
    for idx,df in enumerate(all_xyz,1):
        c=df[['x','y','z']].to_numpy()
        r={'frame':idx}
        if bond: i,j=bond; r['bond']=bond_length(c[i-1],c[j-1])
        if angle: i,j,k=angle; r['angle_deg']=angle_deg(c[i-1],c[j-1],c[k-1])
        if dihedral: i,j,k,l=dihedral; r['dihedral_deg']=dihedral_deg(c[i-1],c[j-1],c[k-1],c[l-1])
        rows.append(r)
    return pd.DataFrame(rows)