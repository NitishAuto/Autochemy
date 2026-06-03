"""
QRO Processor - Processes ORCA output files to extract degenerate orbital information
Converted from bash script to Python
"""

import pandas as pd
import os
from typing import Dict, List, Tuple, Optional


class QROProcessor:
    """Processes ORCA output files to extract orbital degeneracy information."""
    
    def __init__(self, file_path: str):
        """
        Initialize processor with ORCA output file.
        
        Args:
            file_path: Path to ORCA output file (.out)
        """
        self.file_path = file_path
        self.data = []
        self.mag_orbitals = []
        self.orbital_ranges = {}
    
    def extract_degeneracy_info(self) -> pd.DataFrame:
        """
        Extract orbital degeneracy information from ORCA output file.
        
        Returns:
            DataFrame with columns: Orbital, Degeneracy, Energy (AU), Energy (eV)
        """
        self.data = []
        self.mag_orbitals = []
        
        with open(self.file_path, 'r', encoding='utf-8', errors='ignore') as file:
            lines = file.readlines()
        
        # Reverse the lines to mimic `tac`
        lines.reverse()
        
        extracted_lines = []
        target_found = False
        count = 0
        
        # Extract orbital information
        for line in lines:
            if "UHF Corresponding Orbitals" in line:
                target_found = True
                continue
            if target_found:
                if "Orbital Energies of Quasi-Restricted MO's" in line or count >= 4000:
                    break
                extracted_lines.append(line.strip())
                count += 1
        
        # Reverse the collected lines to restore original order
        extracted_lines.reverse()
        extracted_lines = extracted_lines[:-6] if len(extracted_lines) > 6 else extracted_lines
        
        # Parse orbital data
        for line in extracted_lines:
            if not line.strip():
                continue
            try:
                parts = line.split()
                if len(parts) < 6:
                    continue
                
                # Extract the columns
                orbital = int(parts[0].split('(')[0])  # Extract the orbital (before '(')
                degeneracy = int(parts[1][0])  # Remove parentheses from degeneracy
                energy_au = float(parts[3])  # Energy in atomic units (AU)
                energy_ev = float(parts[5])  # Energy in electron volts (eV)
                
                # Append to the list
                self.data.append([orbital, degeneracy, energy_au, energy_ev])
            except (ValueError, IndexError) as e:
                # Skip lines that don't match expected format
                continue
        
        # Extract magnetic orbital information
        start_flag = "(*)  the overlap is weighted by the product of occupation numbers"
        end_flag = " Orbital    Overlap(*)"
        
        capture = False
        for i, line in enumerate(lines):
            if line.strip() == start_flag:
                capture = True
                continue
            if capture and end_flag in line:
                break
            if capture:
                self.mag_orbitals.append(line)
        
        self.mag_orbitals.reverse()
        
        # Convert to DataFrame
        df = pd.DataFrame(self.data, columns=['Orbital', 'Degeneracy', 'Energy (AU)', 'Energy (eV)'])
        return df
    
    def calculate_orbital_ranges(self, df: pd.DataFrame) -> Dict[str, int]:
        """
        Calculate orbital ranges for doubly, singly, and unoccupied orbitals.
        
        Args:
            df: DataFrame with orbital information
            
        Returns:
            Dictionary with keys: d1, d2, s1, s2, total
            d1: doubly-start (first doubly occupied with E <= -1)
            d2: doubly-end (last doubly occupied)
            s1: singly-start (first singly occupied, -1 if none)
            s2: singly-end (last singly occupied, -1 if none)
            total: total number of orbitals
        """
        d1 = 0
        d2 = -1
        s1 = -1
        s2 = -1
        total = len(df)
        
        for index, row in df.iterrows():
            if row["Degeneracy"] == 2:
                if row["Energy (AU)"] <= -1:
                    d1 += 1
                    d2 += 1
                else:
                    d2 += 1
            elif row["Degeneracy"] == 1:
                s2 += 1
        
        if s2 != -1:
            s1 = d2 + 1
            s2 = d2 + s2 + 1
        
        self.orbital_ranges = {
            'd1': d1,
            'd2': d2,
            's1': s1,
            's2': s2,
            'total': total
        }
        
        return self.orbital_ranges
    
    def calculate_unoccupied_range(self, num_unoccupied: int) -> Tuple[int, int]:
        """
        Calculate range for unoccupied orbitals.
        
        Args:
            num_unoccupied: Number of unoccupied orbitals
            
        Returns:
            Tuple of (start, end) for unoccupied orbitals
        """
        if self.orbital_ranges['s1'] == -1:
            # No singly occupied orbitals
            n5 = self.orbital_ranges['d2'] + 1
            n6 = self.orbital_ranges['d2'] + 1 + num_unoccupied
        else:
            # Has singly occupied orbitals
            n5 = self.orbital_ranges['s2'] + 1
            n6 = n5 + num_unoccupied
        
        return (n5, n6)
    
    def get_degenerate_orbitals(self, df: pd.DataFrame) -> Dict[str, List]:
        """
        Group orbitals by degeneracy type.
        
        Args:
            df: DataFrame with orbital information
            
        Returns:
            Dictionary with keys: 'doubly', 'singly', 'unoccupied'
            Each contains list of orbital data
        """
        ranges = self.orbital_ranges
        result = {
            'doubly': [],
            'singly': [],
            'unoccupied': []
        }
        
        for index, row in df.iterrows():
            orbital_num = row['Orbital']
            if ranges['d1'] <= orbital_num <= ranges['d2']:
                result['doubly'].append(row.to_dict())
            elif ranges['s1'] != -1 and ranges['s1'] <= orbital_num <= ranges['s2']:
                result['singly'].append(row.to_dict())
            # Unoccupied will be determined after user input
        
        return result
    
    def save_magnetic_orbitals(self, output_path: str = "mag_orbitals.txt"):
        """Save magnetic orbital information to file."""
        with open(output_path, 'w', encoding='utf-8') as file:
            for item in self.mag_orbitals:
                file.write(item + "\n")


