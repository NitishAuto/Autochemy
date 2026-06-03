#!/bin/bash

d_start=$1
d_end=$2
s_start=$3
s_end=$4
total=$5
tun=$6

module load orca-5.0.3
curr=$(pwd)
mkdir -p orbital/{singly,doubly,unoccupied}
mkdir temp

if [ "$s_start" -eq -1 ]; then
    n5=$((d_end+1))~/
    n6=$((d_end + 1 + tun))
else
    n5=$((s_end+1))
    n6=$((n5 + 1 + tun)) 
fi

cd orbital/doubly/
cp ../../*qro .
loc *qro $d_start $d_end >> $curr/temp/d_loc.out
mkdir plot
cd plot
cp ../*.loc.qro .
splotprime *.loc.qro $d_start $d_end >> $curr/temp/d_plt.out

if [ "$s_start" -ne -1 ]; then
cd ../../singly
cp ../../*qro .
loc *qro $s_start $s_end >> $curr/temp/s_loc.out
mkdir plot
cd plot
cp ../*.loc.qro .
splotprime *.loc.qro $s_start $s_end >> $curr/temp/s_plt.out
fi

cd ../../unoccupied
cp ../../*qro .
loc *qro $n5 $n6 >> $curr/temp/u_loc.out
mkdir plot
cd plot
cp ../*.loc.qro .
splotprime *.loc.qro $n5 $n6 >> $curr/temp/u_plt.out

