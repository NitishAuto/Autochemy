
# ---------- user-supplied tables (kept as you provided) ----------
periodic_table = ["","H","He","Li","Be","B","C","N","O","F","Ne","Na","Mg","Al","Si","P","S","Cl","Ar","K","Ca","Sc","Ti","V","Cr","Mn","Fe","Co","Ni","Cu","Zn","Ga","Ge","As","Se","Br","Kr","Rb","Sr","Y","Zr","Nb","Mo","Tc","Ru","Rh","Pd","Ag","Cd","In","Sn","Sb","Te","I","Xe","Cs","Ba","La","Ce","Pr","Nd","Pm","Sm","Eu","Gd","Tb","Dy","Ho","Er","Tm","Yb","Lu","Hf","Ta","W","Re","Os","Ir","Pt","Au","Hg","Tl","Pb","Bi","Po","At","Rn","Fr","Ra","Ac","Th","Pa","U","Np","Pu","Am","Cm","Bk","Cf","Es","Fm","Md","No","Lr","Rf","Db","Sg","Bh","Hs","Mt","Ds","Rg","Uub","Uut","Uuq","Uup","Uuh","Uus","Uuo"]
#Covalent radii taken from DOI: 10.1039/b801115j
#Everything beyond Cm was set to 1.80
covalent_radii = [0.00,0.32,0.28,1.28,0.96,0.84,0.76,0.71,0.66,0.57,0.58,1.66,1.41,1.21,1.11,1.07,1.05,1.02,1.06,2.03,1.76,1.70,1.60,1.53,1.39,1.61,1.52,1.50,1.24,1.32,1.22,1.22,1.20,1.19,1.20,1.20,1.16,2.20,1.95,1.90,1.75,1.64,1.54,1.47,1.46,1.42,1.39,1.45,1.44,1.42,1.39,1.39,1.38,1.39,1.40,2.44,2.15,2.07,2.04,2.03,2.01,1.99,1.98,1.98,1.96,1.94,1.92,1.92,1.89,1.90,1.87,1.87,1.75,1.70,1.62,1.51,1.44,1.41,1.36,1.36,1.32,1.45,1.46,1.48,1.40,1.50,1.50,2.60,2.21,2.15,2.06,2.00,1.96,1.90,1.87,180,169,1.80,1.80,1.80,1.80,1.80,1.80,1.80,1.80,1.80,1.80,1.80,1.80,1.80,1.80,1.80,1.80,1.80,1.80,1.80,1.80,1.80,1.80]

# ---------- helper functions ----------

def r(sym):
    s = ''.join(ch for ch in str(sym).strip() if ch.isalpha()).title()
    return covalent_radii[periodic_table.index(s)] if s in periodic_table else 1.80
    
def get_bonds(df, fac=1.25):
    elems = df['elem'].tolist()
    coords = df[['x','y','z']].to_numpy()
    n = len(df)

    d = np.linalg.norm(coords[:,None,:] - coords[None,:,:], axis=2)

    pairs = []
    for i in range(n):
        for j in range(i+1, n):
            dij = d[i,j]
            if dij > 0.4 and dij <= fac * (r(elems[i]) + r(elems[j])):
                pairs.append((i+1, j+1, round(float(dij),4)))

    return pd.DataFrame(pairs, columns=['atom1','atom2','distance'])


def get_fragments(df):
    g = {}
    for a, b in zip(df.atom1, df.atom2):
        g.setdefault(a, []).append(b)
        g.setdefault(b, []).append(a)

    visited = set()
    fragments = []

    for node in g:
        if node not in visited:
            stack = [node]
            comp = []
            while stack:
                x = stack.pop()
                if x not in visited:
                    visited.add(x)
                    comp.append(x)
                    stack.extend(g.get(x, []))
            fragments.append(sorted(comp))
    return fragments

def edge_diff(df1, df2):
    e1 = set(tuple(sorted(x)) for x in df1[['atom1','atom2']].to_numpy())
    e2 = set(tuple(sorted(x)) for x in df2[['atom1','atom2']].to_numpy())
    diff = e1 ^ e2                      # symmetric difference
    return pd.DataFrame(list(diff), columns=['atom1','atom2']).sort_values(['atom1','atom2'])

