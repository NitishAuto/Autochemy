"""
ORCA Output File Parser
Parses ORCA output files and extracts key computational chemistry information.
"""

import re
import json
from typing import Dict, List, Optional
from dataclasses import dataclass


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
    
    def get_final_energy(self) -> Optional[float]:
        """Extract final SCF energy (scanning from end)."""
        # Scan from end for FINAL SINGLE POINT ENERGY
        for line in reversed(self.lines):
            if "FINAL SINGLE POINT ENERGY" in line:
                match = re.search(r"([-+]?\d*\.\d+)", line)
                if match:
                    try:
                        return float(match.group(1))
                    except ValueError:
                        pass
                break
        
        # Fallback patterns
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
        # Also check for standard convergence message
        if re.search(r"THE OPTIMIZATION HAS CONVERGED", self.content, re.IGNORECASE):
            return True
        return False
    
    def get_imaginary_modes(self) -> int:
        """Count imaginary vibrational modes."""
        return sum(1 for line in self.lines if "***imaginary mode***" in line.lower())
    
    def get_gibbs_energy_info(self) -> Dict[str, Optional[float]]:
        """Extract Gibbs free energy, enthalpy, and thermal correction."""
        result = {
            'gibbs_energy': None,
            'total_enthalpy': None,
            'thermal_correction': None
        }
        
        # Find Gibbs energy block
        gibbs_block = re.findall(
            r'The Gibbs free energy is G = H - T\*S(?:.*\n){0,13}',
            self.content, re.IGNORECASE | re.MULTILINE
        )
        
        if gibbs_block:
            block = gibbs_block[-1]
            
            # Final Gibbs free energy
            gibbs_match = re.search(r'Final Gibbs free energy.*?([+-]?[0-9]*\.[0-9]+)', block)
            if gibbs_match:
                try:
                    result['gibbs_energy'] = float(gibbs_match.group(1))
                except ValueError:
                    pass
            
            # Total enthalpy
            enthalpy_match = re.search(r'Total enthalpy.*?([+-]?[0-9]*\.[0-9]+)', block)
            if enthalpy_match:
                try:
                    result['total_enthalpy'] = float(enthalpy_match.group(1))
                except ValueError:
                    pass
            
            # Thermal correction (G-E(el))
            thermal_match = re.search(r'G-E\(el\).*?([+-]?[0-9]*\.[0-9]+)', block)
            if thermal_match:
                try:
                    result['thermal_correction'] = float(thermal_match.group(1))
                except ValueError:
                    pass
        
        return result
    
    def get_total_time_hours(self) -> Optional[float]:
        """Extract total run time in hours."""
        for line in self.lines:
            if "TOTAL RUN TIME:" in line:
                # Pattern: "X days Y hours Z minutes W seconds V msec"
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
        
        # Fallback: try simple seconds format
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
        # #region agent log
        try:
            with open(r'e:\projects\25\orca_code\.cursor\debug.log', 'a', encoding='utf-8') as f:
                f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"E","location":"orca_parser.py:179","message":"get_s2_values entry","data":{"total_lines":len(self.lines)},"timestamp":__import__('time').time()*1000})+'\n')
        except: pass
        # #endregion
        
        result = {
            's2_expectation': None,
            's2_ideal': None,
            's2_deviation': None,
        }

        # Use user's approach: scan in reverse for direct matches
        exp = None
        ideal = None
        dev = None
        
        # #region agent log
        try:
            with open(r'e:\projects\25\orca_code\.cursor\debug.log', 'a', encoding='utf-8') as f:
                f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"E","location":"orca_parser.py:192","message":"Starting reverse scan","data":{},"timestamp":__import__('time').time()*1000})+'\n')
        except: pass
        # #endregion
        
        for line in reversed(self.lines):
            if dev is None and "Deviation" in line:
                try:
                    parts = line.split()
                    if parts:
                        dev = float(parts[-1])
                        result['s2_deviation'] = dev
                        # #region agent log
                        try:
                            with open(r'e:\projects\25\orca_code\.cursor\debug.log', 'a', encoding='utf-8') as f:
                                f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"E","location":"orca_parser.py:202","message":"Found deviation","data":{"value":dev,"line":line[:100]},"timestamp":__import__('time').time()*1000})+'\n')
                        except: pass
                        # #endregion
                except (ValueError, IndexError):
                    pass

            elif ideal is None and "Ideal value S*(S+1)" in line:
                try:
                    parts = line.split()
                    if parts:
                        ideal = float(parts[-1])
                        result['s2_ideal'] = ideal
                        # #region agent log
                        try:
                            with open(r'e:\projects\25\orca_code\.cursor\debug.log', 'a', encoding='utf-8') as f:
                                f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"E","location":"orca_parser.py:214","message":"Found ideal","data":{"value":ideal,"line":line[:100]},"timestamp":__import__('time').time()*1000})+'\n')
                        except: pass
                        # #endregion
                except (ValueError, IndexError):
                    pass

            elif exp is None and "Expectation value of <S**2>" in line:
                try:
                    parts = line.split()
                    if parts:
                        exp = float(parts[-1])
                        result['s2_expectation'] = exp
                        # #region agent log
                        try:
                            with open(r'e:\projects\25\orca_code\.cursor\debug.log', 'a', encoding='utf-8') as f:
                                f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"E","location":"orca_parser.py:226","message":"Found expectation","data":{"value":exp,"line":line[:100]},"timestamp":__import__('time').time()*1000})+'\n')
                        except: pass
                        # #endregion
                except (ValueError, IndexError):
                    pass
            
            # Stop if all values found
            if exp is not None and ideal is not None and dev is not None:
                break

        # #region agent log
        try:
            with open(r'e:\projects\25\orca_code\.cursor\debug.log', 'a', encoding='utf-8') as f:
                f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"ALL","location":"orca_parser.py:240","message":"get_s2_values exit","data":{"result":result},"timestamp":__import__('time').time()*1000})+'\n')
        except: pass
        # #endregion
        return result
    
    def get_geometry(self) -> List[Geometry]:
        """Extract optimized geometry coordinates."""
        geometry = []
        in_geometry = False
        
        # Look for geometry section
        geometry_patterns = [
            r"CARTESIAN COORDINATES",
            r"FINAL GEOMETRY",
            r"Coordinates \(Angstroms\)",
        ]
        
        for i, line in enumerate(self.lines):
            # Check if we're entering geometry section
            for pattern in geometry_patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    in_geometry = True
                    break
            
            if in_geometry:
                # Parse coordinate lines: "C 0.000000 0.000000 0.000000"
                coord_match = re.match(r'^\s*(\w+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)', line)
                if coord_match:
                    atom = coord_match.group(1)
                    x = float(coord_match.group(2))
                    y = float(coord_match.group(3))
                    z = float(coord_match.group(4))
                    geometry.append(Geometry(atom, x, y, z))
                elif line.strip() and not re.match(r'^\s*[-=]', line):
                    # Stop if we hit a non-coordinate line (but allow separators)
                    if len(geometry) > 0:
                        break
        
        return geometry
    
    def get_frequencies(self) -> List[Frequency]:
        """Extract vibrational frequencies."""
        frequencies = []
        in_freq_section = False
        
        for i, line in enumerate(self.lines):
            if re.search(r"VIBRATIONAL FREQUENCIES", line, re.IGNORECASE):
                in_freq_section = True
                continue
            
            if in_freq_section:
                # Pattern: "0:   1234.56 cm**-1   12.34 IR"
                freq_match = re.match(
                    r'^\s*\d+:\s+(-?\d+\.\d+)\s+cm\*\*-1\s+(\d+\.\d+)\s+(\w+)',
                    line, re.IGNORECASE
                )
                if freq_match:
                    freq = float(freq_match.group(1))
                    intensity = float(freq_match.group(2))
                    symmetry = freq_match.group(3)
                    frequencies.append(Frequency(freq, intensity, symmetry))
                elif line.strip() and re.search(r'^\s*[-=]', line):
                    if len(frequencies) > 0:
                        break
        
        return frequencies
    
    def get_normal_termination(self) -> bool:
        """Check if ORCA terminated normally."""
        return "****ORCA TERMINATED NORMALLY****" in self.content
    
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

