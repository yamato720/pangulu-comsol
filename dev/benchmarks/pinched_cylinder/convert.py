#!/usr/bin/env python3
"""Read the first complete PETSc binary A/b/x triplet without pruning stored zeros."""
from pathlib import Path
import argparse
import hashlib
import json
import numpy as np
from scipy.sparse import csr_matrix

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'data/runs/pinched-cylinder_20261116T184505'

def width(path,classid):
    with path.open('rb') as stream:prefix=stream.read(8)
    if len(prefix)>=4 and int.from_bytes(prefix[:4],'big',signed=True)==classid:return 4
    if len(prefix)==8 and int.from_bytes(prefix,'big',signed=True)==classid:return 8
    raise ValueError(f'Unrecognized PETSc class header: {path} {prefix.hex()}')

def vector(path):
    w=width(path,1211214);integer=f'>i{w}'
    header=np.fromfile(path,dtype=integer,count=2);assert header[0]==1211214
    count=int(header[1]);v=np.fromfile(path,dtype='>f8',count=count,offset=2*w).astype(np.float64)
    assert len(v)==count and np.isfinite(v).all()
    return v,2*w+8*count

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(8*1024*1024),b''):h.update(block)
    return h.hexdigest()

def main():
    p=argparse.ArgumentParser();p.add_argument('application');p.add_argument('case');a=p.parse_args()
    source=OUT/a.application;path=source/'A.petsc';w=width(path,1211216);integer=f'>i{w}'
    header=np.fromfile(path,dtype=integer,count=4);assert header[0]==1211216
    n,m,nz=map(int,header[1:]);assert n==m and n>0 and nz>0
    rows=np.fromfile(path,dtype=integer,count=n,offset=4*w).astype(np.int64)
    assert np.all(rows>=0) and rows.sum()==nz
    rp=np.empty(n+1,dtype=np.int64);rp[0]=0;np.cumsum(rows,out=rp[1:])
    ci=np.fromfile(path,dtype=integer,count=nz,offset=(4+n)*w).astype(np.int32)
    values=np.fromfile(path,dtype='>f8',count=nz,offset=(4+n+nz)*w).astype(np.float64)
    assert len(ci)==len(values)==nz and np.isfinite(values).all() and ci.min()>=0 and ci.max()<n
    csr=csr_matrix((values,ci,rp),shape=(n,n));assert csr.has_canonical_format
    csc=csr.tocsc();assert csc.nnz==nz and csc.has_canonical_format
    b,b_bytes=vector(source/'b.petsc');x,x_bytes=vector(source/'x.petsc');assert b.size==x.size==n
    residual=float(np.linalg.norm(csr@x-b)/np.linalg.norm(b))
    assert np.isfinite(residual) and residual<=1e-8,residual
    target=OUT/'datasets'/a.case;target.mkdir(parents=True)
    csc.indptr.astype('<u8').tofile(target/'ptr.u64');csc.indices.astype('<i4').tofile(target/'idx.i32');csc.data.astype('<f8').tofile(target/'values.bin')
    b.astype('<f8').tofile(target/'physical_rhs.bin');x.astype('<f8').tofile(target/'application_linear_solution.bin')
    metadata={'case':'pinched-cylinder','n':n,'nnz':nz,'dtype':'real','type':'general','physical_rhs':True,
              'stored_explicit_zeros':int(np.count_nonzero(values==0)),'source':str(source),'PETSc_integer_bytes':w,
              'first_matrix_bytes':(4+n+nz)*w+nz*8,'first_rhs_bytes':b_bytes,'first_solution_bytes':x_bytes,
              'source_binary_sizes':{name:(source/name).stat().st_size for name in ['A.petsc','b.petsc','x.petsc']},
              'captured_solution_relative_residual':residual,'timeout_seconds':180,'trace_timeout_seconds':300,'single_gpu_values_buffer':True,'rhs_columns':1,
              'note':'First matching A,b,x record from complete upstream MOOSE application. CSR-to-CSC preserves every stored item, including zeros. Original point load and boundary rows retained.'}
    if a.case=='million':assert (n,nz)==(1017005,45630025),(n,nz)
    (target/'metadata.json').write_text(json.dumps(metadata,indent=2)+'\n')
    hashes={str(q.relative_to(OUT)):digest(q) for q in [*(source/name for name in ['A.petsc','b.petsc','x.petsc']),*target.iterdir()] if q.is_file()}
    (target/'sha256.json').write_text(json.dumps(hashes,indent=2)+'\n')
    print(json.dumps(metadata,indent=2),flush=True)

if __name__=='__main__':main()
