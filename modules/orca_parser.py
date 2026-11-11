"""
ORCA Output File Parser
Parses ORCA output files and extracts key computational chemistry information.
"""

import re
from typing import Dict, List, Optional
from dataclasses import dataclass, field


HARTREE_TO_KCAL = 627.509474
HARTREE_TO_KJ = 2625.499639
HARTREE_TO_EV = 27.211386
CM1_PER_EV = 8065.54429

_FLOAT = r"[-+]?\d*\.\d+(?:[Ee][-+]?\d+)?"


@dataclass
class Geometry:
    """Represents atomic geometry information."""
    atom: str
    x: float
    y: float
    z: float


@dataclass
class Frequency:
    """Represents vibrational frequency information."""
    frequency: float
    intensity: float
    symmetry: str = ""
    mode: int = -1


@dataclass
class Orbital:
    """A single molecular orbital energy level."""
    index: int
    occupation: float
    energy_eh: float
    energy_ev: float


@dataclass
class AtomicCharge:
    """Population-analysis charge (and optional spin population) of one atom."""
    index: int
    atom: str
    charge: float
    spin: Optional[float] = None


@dataclass
class ExcitedState:
    """An excited state from a TD-DFT / CIS absorption spectrum."""
    state: str
    energy_ev: float
    wavelength_nm: float
    fosc: float


@dataclass
class OptCycle:
    """Convergence data of one geometry-optimization cycle."""
    cycle: int
    energy: Optional[float] = None
    items: Dict[str, tuple] = field(default_factory=dict)  # name -> (value, tolerance, converged)


class ORCAParser:
    """Parser for ORCA output files."""

    def __init__(self, filepath: str):
        """Initialize parser with ORCA output file path."""
        self.filepath = filepath
        self.content = ""
        self.lines = []
        self._load_file()

    def _load_file(self):
        """Load the ORCA output file."""
        try:
            with open(self.filepath, 'r', encoding='utf-8', errors='ignore') as f:
                self.content = f.read()
                self.lines = self.content.split('\n')
        except Exception as e:
            raise IOError(f"Error reading file: {e}")

    def _last_index(self, marker: str) -> int:
        """Line index of the last line containing marker, or -1."""
        for i in range(len(self.lines) - 1, -1, -1):
            if marker in self.lines[i]:
                return i
        return -1

    # ------------------------------------------------------------------
    # Energies & status
    # ------------------------------------------------------------------
    def get_final_energy(self) -> Optional[float]:
        """Extract final SCF energy (scanning from end)."""
        for line in reversed(self.lines):
            if "FINAL SINGLE POINT ENERGY" in line:
                match = re.search(r"([-+]?\d*\.\d+)", line)
                if match:
                    try:
                        return float(match.group(1))
                    except ValueError:
                        pass
                break

        patterns = [
            r"FINAL SINGLE POINT ENERGY\s+(-?\d+\.\d+)",
            r"Total Energy\s+:\s+(-?\d+\.\d+)",
            r"E\(SCF\)\s+=\s+(-?\d+\.\d+)",
            r"FINAL ENERGY\s+(-?\d+\.\d+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, self.content, re.IGNORECASE)
            if match:
                try:
                    return float(match.group(1))
                except ValueError:
                    continue
        return None

    def get_all_sp_energies(self) -> List[float]:
        """All FINAL SINGLE POINT ENERGY values in order (one per opt cycle)."""
        out = []
        for line in self.lines:
            if "FINAL SINGLE POINT ENERGY" in line:
                m = re.search(r"(-?\d+\.\d+)", line)
                if m:
                    out.append(float(m.group(1)))
        return out

    def get_electronic_energy(self) -> Optional[float]:
        """Extract last occurrence of Electronic energy."""
        for line in reversed(self.lines):
            if "Electronic energy" in line:
                match = re.search(r'[+-]?[0-9]*\.[0-9]+', line)
                if match:
                    try:
                        return float(match.group())
                    except ValueError:
                        pass
                break
        return None

    def get_geo_opt_converged(self) -> bool:
        """Check if geometry optimization converged (looks for HURRAY)."""
        for line in self.lines:
            if "HURRAY" in line.upper():
                return True
        if re.search(r"THE OPTIMIZATION HAS CONVERGED", self.content, re.IGNORECASE):
            return True
        return False

    def get_imaginary_modes(self) -> int:
        """Count imaginary vibrational modes (last frequency block only)."""
        start = self._last_index("VIBRATIONAL FREQUENCIES")
        lines = self.lines[start:] if start >= 0 else self.lines
        count = 0
        for line in lines:
            if "***imaginary mode***" in line.lower():
                count += 1
            elif count and not line.strip():
                break
        return count

    def get_gibbs_energy_info(self) -> Dict[str, Optional[float]]:
        """Extract Gibbs free energy, enthalpy, and thermal correction."""
        result = {
            'gibbs_energy': None,
            'total_enthalpy': None,
            'thermal_correction': None
        }

        gibbs_block = re.findall(
            r'The Gibbs free energy is G = H - T\*S(?:.*\n){0,13}',
            self.content, re.IGNORECASE | re.MULTILINE
        )

        if gibbs_block:
            block = gibbs_block[-1]

            gibbs_match = re.search(r'Final Gibbs free energy.*?([+-]?[0-9]*\.[0-9]+)', block)
            if gibbs_match:
                try:
                    result['gibbs_energy'] = float(gibbs_match.group(1))
                except ValueError:
                    pass

            enthalpy_match = re.search(r'Total enthalpy.*?([+-]?[0-9]*\.[0-9]+)', block)
            if enthalpy_match:
                try:
                    result['total_enthalpy'] = float(enthalpy_match.group(1))
                except ValueError:
                    pass

            thermal_match = re.search(r'G-E\(el\).*?([+-]?[0-9]*\.[0-9]+)', block)
            if thermal_match:
                try:
                    result['thermal_correction'] = float(thermal_match.group(1))
                except ValueError:
                    pass

        return result

    def get_thermochemistry(self) -> Dict[str, Optional[float]]:
        """Full thermochemistry summary (all values in Eh, temperature in K)."""
        keys = {
            'temperature': r"^Temperature\s+\.\.\.\s+(" + _FLOAT + r")\s*K",
            'pressure': r"^Pressure\s+\.\.\.\s+(" + _FLOAT + r")\s*atm",
            'electronic_energy': r"^Electronic energy\s+\.\.\.\s+(" + _FLOAT + r")",
            'zpe': r"^Zero point energy\s+\.\.\.\s+(" + _FLOAT + r")",
            'thermal_vib': r"^Thermal vibrational correction\s+\.\.\.\s+(" + _FLOAT + r")",
            'thermal_rot': r"^Thermal rotational correction\s+\.\.\.\s+(" + _FLOAT + r")",
            'thermal_trans': r"^Thermal translational correction\s+\.\.\.\s+(" + _FLOAT + r")",
            'total_thermal_energy': r"^Total thermal energy\s+(" + _FLOAT + r")",
            'total_correction': r"^Total correction\s+(" + _FLOAT + r")",
            'thermal_enthalpy_corr': r"^Thermal Enthalpy correction\s+\.\.\.\s+(" + _FLOAT + r")",
            'total_enthalpy': r"^Total Enthalpy\s+\.\.\.\s+(" + _FLOAT + r")",
            'entropy_el': r"^Electronic entropy\s+\.\.\.\s+(" + _FLOAT + r")",
            'entropy_vib': r"^Vibrational entropy\s+\.\.\.\s+(" + _FLOAT + r")",
            'entropy_rot': r"^Rotational entropy\s+\.\.\.\s+(" + _FLOAT + r")",
            'entropy_trans': r"^Translational entropy\s+\.\.\.\s+(" + _FLOAT + r")",
            'entropy_term': r"^Final entropy term\s+\.\.\.\s+(" + _FLOAT + r")",
            'gibbs_energy': r"^Final Gibbs free energy\s+\.\.\.\s+(" + _FLOAT + r")",
            'g_minus_eel': r"^G-E\(el\)\s+\.\.\.\s+(" + _FLOAT + r")",
        }
        result = {k: None for k in keys}
        start = self._last_index("THERMOCHEMISTRY AT")
        if start < 0:
            return result
        block = "\n".join(self.lines[start:])
        for k, pat in keys.items():
            m = re.search(pat, block, re.MULTILINE)
            if m:
                result[k] = float(m.group(1))
        return result

    def get_total_time_hours(self) -> Optional[float]:
        """Extract total run time in hours."""
        for line in self.lines:
            if "TOTAL RUN TIME:" in line:
                match = re.search(
                    r'(\d+) days (\d+) hours (\d+) minutes (\d+) seconds (\d+) msec',
                    line
                )
                if match:
                    try:
                        days, hours, minutes, seconds, _ = map(int, match.groups())
                        total_hours = round(
                            days * 24 + hours + minutes / 60 + seconds / 3600, 3
                        )
                        return total_hours
                    except ValueError:
                        pass
                break

        match = re.search(r"TOTAL RUN TIME:\s+(\d+\.\d+)\s+sec", self.content, re.IGNORECASE)
        if match:
            try:
                seconds = float(match.group(1))
                return round(seconds / 3600, 3)
            except ValueError:
                pass

        return None

    def get_s2_values(self) -> Dict[str, Optional[float]]:
        """Extract S**2 values using reverse scan approach (more reliable)."""
        result = {
            's2_expectation': None,
            's2_ideal': None,
            's2_deviation': None,
        }

        exp = None
        ideal = None
        dev = None

        for line in reversed(self.lines):
            if dev is None and "Deviation" in line:
                try:
                    parts = line.split()
                    if parts:
                        dev = float(parts[-1])
                        result['s2_deviation'] = dev
                except (ValueError, IndexError):
                    pass

            elif ideal is None and "Ideal value S*(S+1)" in line:
                try:
                    parts = line.split()
                    if parts:
                        ideal = float(parts[-1])
                        result['s2_ideal'] = ideal
                except (ValueError, IndexError):
                    pass

            elif exp is None and "Expectation value of <S**2>" in line:
                try:
                    parts = line.split()
                    if parts:
                        exp = float(parts[-1])
                        result['s2_expectation'] = exp
                except (ValueError, IndexError):
                    pass

            if exp is not None and ideal is not None and dev is not None:
                break

        return result

    # ------------------------------------------------------------------
    # Job setup
    # ------------------------------------------------------------------
    def get_program_version(self) -> Optional[str]:
        m = re.search(r"Program Version\s+(\S+)", self.content)
        return m.group(1) if m else None

    def get_input_lines(self) -> List[str]:
        """The echoed input file (lines between INPUT FILE and END OF INPUT)."""
        out = []
        for line in self.lines:
            m = re.match(r"^\|\s*\d+>\s?(.*)$", line)
            if m:
                text = m.group(1).rstrip()
                if "****END OF INPUT****" in text:
                    break
                out.append(text)
            elif out and "END OF INPUT" in line:
                break
        return out

    def get_keywords(self) -> List[str]:
        """Simple-input keywords from all '!' lines."""
        kws = []
        for line in self.get_input_lines():
            s = line.strip()
            if s.startswith("!"):
                kws.extend(s[1:].split())
        return kws

    def get_job_info(self) -> Dict[str, Optional[object]]:
        info = {
            'version': self.get_program_version(),
            'keywords': " ".join(self.get_keywords()),
            'charge': None,
            'multiplicity': None,
            'n_electrons': None,
            'n_basis': None,
            'n_atoms': None,
            'hf_type': None,
        }
        pats = {
            'charge': r"Total Charge\s+Charge\s+\.+\s+(-?\d+)",
            'multiplicity': r"Multiplicity\s+Mult\s+\.+\s+(\d+)",
            'n_electrons': r"Number of Electrons\s+NEL\s+\.+\s+(\d+)",
            'n_basis': r"Number of basis functions\s+\.+\s+(\d+)",
            'n_atoms': r"Number of atoms\s+\.+\s+(\d+)",
        }
        for k, pat in pats.items():
            m = re.search(pat, self.content)
            if m:
                info[k] = int(m.group(1))
        m = re.search(r"Hartree-Fock type\s+HFTyp\s+\.+\s+(\w+)", self.content)
        if m:
            info['hf_type'] = m.group(1)
        if info['charge'] is None or info['multiplicity'] is None:
            for line in self.get_input_lines():
                m = re.match(r"^\s*\*\s*(?:xyz|xyzfile|int|gzmt|pdbfile)\s+(-?\d+)\s+(\d+)", line, re.I)
                if m:
                    info['charge'] = info['charge'] if info['charge'] is not None else int(m.group(1))
                    info['multiplicity'] = info['multiplicity'] if info['multiplicity'] is not None else int(m.group(2))
                    break
        return info

    def get_scf_info(self) -> Dict[str, object]:
        cycles = [int(x) for x in re.findall(r"SCF CONVERGED AFTER\s+(\d+)\s+CYCLES", self.content)]
        not_conv = len(re.findall(r"SCF NOT CONVERGED", self.content))
        return {'cycles': cycles, 'not_converged': not_conv}

    def get_job_type(self) -> str:
        kws = {k.upper() for k in self.get_keywords()}
        parts = []
        if kws & {"OPT", "COPT", "ZOPT", "GDIIS-OPT", "TIGHTOPT", "VERYTIGHTOPT", "LOOSEOPT", "OPTTS"}:
            parts.append("TS Opt" if "OPTTS" in kws else "Optimisation")
        if "Geometry convergence" in self.content and not parts:
            parts.append("Optimisation")
        if kws & {"FREQ", "NUMFREQ", "ANFREQ"} or "VIBRATIONAL FREQUENCIES" in self.content:
            parts.append("Frequency")
        if "ABSORPTION SPECTRUM" in self.content:
            parts.append("TD-DFT")
        if "NEB" in " ".join(kws):
            parts.append("NEB")
        if "RELAXED SURFACE SCAN" in self.content:
            parts.append("Scan")
        return " + ".join(parts) if parts else "Single Point"

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------
    def get_trajectory(self) -> List[List[Geometry]]:
        """All CARTESIAN COORDINATES (ANGSTROEM) blocks in order."""
        frames = []
        i, n = 0, len(self.lines)
        while i < n:
            if "CARTESIAN COORDINATES (ANGSTROEM)" in self.lines[i]:
                frame = []
                j = i + 1
                while j < n:
                    line = self.lines[j]
                    m = re.match(r'^\s*([A-Za-z]{1,3})\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s*$', line)
                    if m:
                        frame.append(Geometry(m.group(1), float(m.group(2)), float(m.group(3)), float(m.group(4))))
                    elif re.match(r'^\s*-+\s*$', line) and not frame:
                        pass
                    elif frame or line.strip():
                        break
                    j += 1
                if frame:
                    frames.append(frame)
                i = j
            else:
                i += 1
        return frames

    def get_geometry(self) -> List[Geometry]:
        """Extract the last (optimized) geometry."""
        frames = self.get_trajectory()
        if frames:
            return frames[-1]

        geometry = []
        in_geometry = False
        geometry_patterns = [
            r"CARTESIAN COORDINATES",
            r"FINAL GEOMETRY",
            r"Coordinates \(Angstroms\)",
        ]
        for line in self.lines:
            for pattern in geometry_patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    in_geometry = True
                    break
            if in_geometry:
                coord_match = re.match(r'^\s*(\w+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)', line)
                if coord_match:
                    geometry.append(Geometry(coord_match.group(1), float(coord_match.group(2)),
                                             float(coord_match.group(3)), float(coord_match.group(4))))
                elif line.strip() and not re.match(r'^\s*[-=]', line):
                    if len(geometry) > 0:
                        break
        return geometry

    def get_opt_cycles(self) -> List[OptCycle]:
        """Per-cycle geometry convergence criteria, paired with cycle energies."""
        cycles = []
        energies = self.get_all_sp_energies()
        i, n = 0, len(self.lines)
        while i < n:
            if "|Geometry convergence|" in self.lines[i]:
                cyc = OptCycle(cycle=len(cycles) + 1)
                j = i + 1
                while j < n and j < i + 20:
                    m = re.match(r'^\s+(Energy change|RMS gradient|MAX gradient|RMS step|MAX step)\s+(' + _FLOAT +
                                 r')\s+(' + _FLOAT + r')\s+(YES|NO)', self.lines[j])
                    if m:
                        cyc.items[m.group(1)] = (float(m.group(2)), float(m.group(3)), m.group(4) == "YES")
                    elif "Max(Dihed)" in self.lines[j] or (cyc.items and re.match(r'^\s*-{10,}', self.lines[j])):
                        break
                    j += 1
                if len(cycles) < len(energies):
                    cyc.energy = energies[len(cycles)]
                cycles.append(cyc)
                i = j
            else:
                i += 1
        return cycles

    # ------------------------------------------------------------------
    # Vibrations
    # ------------------------------------------------------------------
    def get_frequencies(self) -> List[Frequency]:
        """Extract vibrational frequencies (last block) with IR intensities in km/mol."""
        frequencies = []

        start = self._last_index("VIBRATIONAL FREQUENCIES")
        if start >= 0:
            for line in self.lines[start + 1:]:
                freq_match = re.match(r'^\s*(\d+):\s+(-?\d+\.\d+)\s+cm\*\*-1', line, re.IGNORECASE)
                if freq_match:
                    mode_idx = int(freq_match.group(1))
                    freq = float(freq_match.group(2))
                    while len(frequencies) <= mode_idx:
                        frequencies.append(Frequency(0.0, 0.0, "", len(frequencies)))
                    frequencies[mode_idx] = Frequency(freq, 0.0, "", mode_idx)
                elif frequencies and not line.strip():
                    break

        start = self._last_index("IR SPECTRUM")
        if start >= 0 and frequencies:
            seen = False
            for line in self.lines[start + 1:]:
                m = re.match(r'^\s*(\d+):\s+(.*)$', line)
                if m:
                    seen = True
                    mode_idx = int(m.group(1))
                    nums = re.findall(_FLOAT, m.group(2).split("(")[0])
                    if len(nums) >= 3:
                        intensity = float(nums[2])  # freq, eps, Int(km/mol), T**2
                    elif len(nums) == 2:
                        intensity = float(nums[1])
                    else:
                        continue
                    if mode_idx < len(frequencies):
                        frequencies[mode_idx].intensity = intensity
                elif seen and not line.strip():
                    break

        return [f for f in frequencies if not (abs(f.frequency) < 0.001 and f.intensity < 0.001)]

    def get_normal_modes(self) -> Dict[int, List[float]]:
        """Normal-mode displacement vectors: mode index -> flat [x0,y0,z0,x1,...]."""
        start = self._last_index("NORMAL MODES")
        if start < 0:
            return {}
        modes: Dict[int, List[float]] = {}
        cols: List[int] = []
        started = False
        for line in self.lines[start + 1:]:
            s = line.strip()
            if not s:
                continue
            if re.match(r'^(\d+\s+)*\d+$', s):
                cols = [int(x) for x in s.split()]
                for c in cols:
                    modes.setdefault(c, [])
                started = True
                continue
            m = re.match(r'^\s*(\d+)((?:\s+' + _FLOAT + r')+)\s*$', line)
            if m and cols:
                vals = [float(v) for v in m.group(2).split()]
                for c, v in zip(cols, vals):
                    modes[c].append(v)
            elif started:
                break
        return modes

    # ------------------------------------------------------------------
    # Electronic structure
    # ------------------------------------------------------------------
    def get_orbitals(self) -> Dict[str, List[Orbital]]:
        """Last ORBITAL ENERGIES block. Keys: 'alpha' (or restricted) and 'beta'."""
        start = self._last_index("ORBITAL ENERGIES")
        result = {'alpha': [], 'beta': []}
        if start < 0:
            return result
        current = 'alpha'
        seen_any = False
        for line in self.lines[start + 2:]:
            if "SPIN UP ORBITALS" in line:
                current = 'alpha'
                continue
            if "SPIN DOWN ORBITALS" in line:
                current = 'beta'
                continue
            m = re.match(r'^\s*(\d+)\s+(' + _FLOAT + r')\s+(' + _FLOAT + r')\s+(' + _FLOAT + r')\s*$', line)
            if m:
                seen_any = True
                result[current].append(Orbital(int(m.group(1)), float(m.group(2)),
                                               float(m.group(3)), float(m.group(4))))
            elif "NO   OCC" in line or not line.strip():
                continue
            elif seen_any:
                break
        return result

    @staticmethod
    def frontier(orbs: List[Orbital]) -> Dict[str, Optional[float]]:
        """HOMO / LUMO energies (eV) and indices for one spin channel."""
        homo = lumo = None
        for o in orbs:
            if o.occupation > 0.1:
                homo = o
            elif lumo is None and homo is not None:
                lumo = o
        return {
            'homo_ev': homo.energy_ev if homo else None,
            'lumo_ev': lumo.energy_ev if lumo else None,
            'homo_idx': homo.index if homo else None,
            'lumo_idx': lumo.index if lumo else None,
            'gap_ev': (lumo.energy_ev - homo.energy_ev) if (homo and lumo) else None,
        }

    def get_homo_lumo(self) -> Dict[str, Optional[float]]:
        """Frontier orbital summary; for open shell uses the smaller-gap channel."""
        orbs = self.get_orbitals()
        a = self.frontier(orbs['alpha'])
        if not orbs['beta']:
            return a
        b = self.frontier(orbs['beta'])
        homo = max([v for v in (a['homo_ev'], b['homo_ev']) if v is not None], default=None)
        lumo = min([v for v in (a['lumo_ev'], b['lumo_ev']) if v is not None], default=None)
        return {
            'homo_ev': homo, 'lumo_ev': lumo,
            'homo_idx': a['homo_idx'], 'lumo_idx': a['lumo_idx'],
            'gap_ev': (lumo - homo) if (homo is not None and lumo is not None) else None,
        }

    def _get_charges(self, header: str) -> List[AtomicCharge]:
        start = -1
        for i in range(len(self.lines) - 1, -1, -1):
            if self.lines[i].strip().startswith(header):
                start = i
                break
        if start < 0:
            return []
        out = []
        for line in self.lines[start + 2:]:
            m = re.match(r'^\s*(\d+)\s+([A-Za-z]{1,3})\s*:\s*(' + _FLOAT + r')(?:\s+(' + _FLOAT + r'))?\s*$', line)
            if m:
                spin = float(m.group(4)) if m.group(4) else None
                out.append(AtomicCharge(int(m.group(1)), m.group(2), float(m.group(3)), spin))
            elif out:
                break
        return out

    def get_mulliken_charges(self) -> List[AtomicCharge]:
        return self._get_charges("MULLIKEN ATOMIC CHARGES")

    def get_loewdin_charges(self) -> List[AtomicCharge]:
        return self._get_charges("LOEWDIN ATOMIC CHARGES")

    def get_dipole(self) -> Optional[Dict[str, float]]:
        vec = None
        mag = None
        for line in reversed(self.lines):
            if mag is None and "Magnitude (Debye)" in line:
                m = re.search(r"(" + _FLOAT + r")\s*$", line)
                if m:
                    mag = float(m.group(1))
            elif mag is not None and "Total Dipole Moment" in line:
                vals = re.findall(_FLOAT, line)
                if len(vals) >= 3:
                    vec = [float(v) for v in vals[:3]]
                break
        if mag is None:
            return None
        return {'x_au': vec[0] if vec else None, 'y_au': vec[1] if vec else None,
                'z_au': vec[2] if vec else None, 'debye': mag}

    def get_excited_states(self) -> List[ExcitedState]:
        """Absorption spectrum via transition electric dipole moments (ORCA 4-6)."""
        start = self._last_index("ABSORPTION SPECTRUM VIA TRANSITION ELECTRIC DIPOLE MOMENTS")
        if start < 0:
            return []
        out = []
        for line in self.lines[start + 1:]:
            if "->" in line:
                m = re.match(r'^\s*\S+\s+->\s+(\S+)\s+(' + _FLOAT + r')\s+(' + _FLOAT + r')\s+(' + _FLOAT +
                             r')\s+(' + _FLOAT + r')', line)
                if m:
                    out.append(ExcitedState(m.group(1), float(m.group(2)), float(m.group(4)), float(m.group(5))))
                    continue
            m = re.match(r'^\s*(\d+)\s+(' + _FLOAT + r')\s+(' + _FLOAT + r')\s+(' + _FLOAT + r')', line)
            if m:
                cm1 = float(m.group(2))
                out.append(ExcitedState(m.group(1), cm1 / CM1_PER_EV, float(m.group(3)), float(m.group(4))))
            elif out and not line.strip():
                break
        return out

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------
    def get_normal_termination(self) -> bool:
        """Check if ORCA terminated normally."""
        return "****ORCA TERMINATED NORMALLY****" in self.content

    def get_warnings(self) -> List[tuple]:
        """Unique warning lines with occurrence counts, in first-seen order."""
        counts: Dict[str, int] = {}
        for line in self.lines:
            s = line.strip()
            if s.upper().startswith("WARNING") and len(s) > 8:
                counts[s] = counts.get(s, 0) + 1
        return list(counts.items())

    def get_errors(self) -> List[str]:
        errs = []
        for line in self.lines:
            s = line.strip()
            if ("error termination" in s.lower() or "ABORTING THE RUN" in s.upper()
                    or re.search(r"\bERROR\b", s) or "SCF NOT CONVERGED" in s):
                if s not in errs:
                    errs.append(s)
        return errs

    def get_status(self) -> str:
        """One-word job status: Normal, Error, Running/Incomplete."""
        if self.get_normal_termination():
            return "Normal"
        if any("error termination" in e.lower() or "ABORT" in e.upper() for e in self.get_errors()):
            return "Error"
        return "Incomplete"

    # ------------------------------------------------------------------
    def get_all_info(self) -> Dict:
        """Extract all available information."""
        gibbs_info = self.get_gibbs_energy_info()
        s2_info = self.get_s2_values()

        return {
            'filepath': self.filepath,
            'final_energy': self.get_final_energy(),
            'electronic_energy': self.get_electronic_energy(),
            'geometry': self.get_geometry(),
            'frequencies': self.get_frequencies(),
            'time_hours': self.get_total_time_hours(),
            'geo_opt_converged': self.get_geo_opt_converged(),
            'imaginary_modes': self.get_imaginary_modes(),
            'gibbs_energy': gibbs_info['gibbs_energy'],
            'total_enthalpy': gibbs_info['total_enthalpy'],
            'thermal_correction': gibbs_info['thermal_correction'],
            's2_expectation': s2_info['s2_expectation'],
            's2_ideal': s2_info['s2_ideal'],
            's2_deviation': s2_info['s2_deviation'],
            'normal_termination': self.get_normal_termination(),
        }

    def get_full_report(self) -> Dict:
        """Everything the Output Viewer shows, in one pass."""
        info = self.get_all_info()
        info.update({
            'job': self.get_job_info(),
            'job_type': self.get_job_type(),
            'input_lines': self.get_input_lines(),
            'scf': self.get_scf_info(),
            'sp_energies': self.get_all_sp_energies(),
            'opt_cycles': self.get_opt_cycles(),
            'trajectory': self.get_trajectory(),
            'normal_modes': self.get_normal_modes(),
            'orbitals': self.get_orbitals(),
            'frontier': self.get_homo_lumo(),
            'mulliken': self.get_mulliken_charges(),
            'loewdin': self.get_loewdin_charges(),
            'dipole': self.get_dipole(),
            'thermo': self.get_thermochemistry(),
            'excited_states': self.get_excited_states(),
            'warnings': self.get_warnings(),
            'errors': self.get_errors(),
            'status': self.get_status(),
        })
        return info
