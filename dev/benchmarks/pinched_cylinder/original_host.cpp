// Internal test of the unmodified public case adapter, including its HOST CSC path.
#include "pangulu_solver.hpp"
#include <fstream>
#include <iostream>
#include <iomanip>
#include <vector>
#include <cmath>
#include <link.h>
#include <cstring>
template<class T>std::vector<T>load(const std::string&p){std::ifstream s(p,std::ios::binary|std::ios::ate);if(!s)throw std::runtime_error(p);size_t bytes=s.tellg();if(bytes%sizeof(T))throw std::runtime_error("width");std::vector<T>v(bytes/sizeof(T));s.seekg(0);s.read((char*)v.data(),bytes);if(!s)throw std::runtime_error("read");return v;}
static int libs(dl_phdr_info*p,size_t,void*){if(p->dlpi_name&&(strstr(p->dlpi_name,"pangulu")||strstr(p->dlpi_name,"cudart")||strstr(p->dlpi_name,"cublas")))std::cout<<"LOADED "<<p->dlpi_name<<std::endl;return 0;}
int main(int argc,char**argv){try{
 if(argc!=2)return 2;std::string root=argv[1];auto cp=load<uint64_t>(root+"/ptr.u64");auto ri=load<int32_t>(root+"/idx.i32");auto a=load<double>(root+"/values.bin");auto b=load<double>(root+"/physical_rhs.bin");auto ref=load<double>(root+"/application_linear_solution.bin");
 size_t n=cp.size()-1,nz=ri.size();if(a.size()!=nz||b.size()!=n||ref.size()!=n)throw std::runtime_error("shape");
 std::vector<int64_t>rp(n+1),next;for(auto r:ri)rp[r+1]++;for(size_t r=0;r<n;r++)rp[r+1]+=rp[r];next=rp;
 std::vector<int32_t>ci(nz);std::vector<double>av(nz),x(n);
 for(size_t col=0;col<n;col++)for(uint64_t k=cp[col];k<cp[col+1];k++){size_t z=next[ri[k]]++;ci[z]=col;av[z]=a[k];}
 std::cout<<std::setprecision(17)<<"HOST_MATRIX n="<<n<<" nnz="<<nz<<std::endl;
 solved::Solver solver;solver.factor(n,rp,ci,av);solver.solve(b.data(),x.data());
 long double e=0,d=0;for(size_t i=0;i<n;i++){long double delta=x[i]-ref[i];e+=delta*delta;d+=(long double)ref[i]*ref[i];}
 double error=std::sqrt(e/d);if(!std::isfinite(error)||error>1e-6)throw std::runtime_error("MUMPS comparison");
 std::cout<<"HOST_RESULT passed=true relative_l2_vs_MUMPS="<<error<<std::endl;dl_iterate_phdr(libs,nullptr);return 0;
 }catch(const std::exception&e){std::cerr<<"HOST_ERROR "<<e.what()<<std::endl;return 1;}}
