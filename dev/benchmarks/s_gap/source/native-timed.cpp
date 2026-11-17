#include <cudss.h>
#include <dlfcn.h>
#include <cstdio>
#include <cstdlib>
#include <time.h>
#include <stdint.h>
#include <unistd.h>
static uint64_t stamp(){timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return (uint64_t)t.tv_sec*1000000000+t.tv_nsec;}
struct NvSpan {const char*kind;int phase;uint64_t data,begin;NvSpan(const char*k,int p,uint64_t d):kind(k),phase(p),data(d),begin(stamp()){}~NvSpan(){fprintf(stderr,"NV_API_SPAN {\"kind\":\"%s\",\"phase\":%d,\"data\":%llu,\"begin_ns\":%llu,\"end_ns\":%llu}\n",kind,phase,(unsigned long long)data,(unsigned long long)begin,(unsigned long long)stamp());}};

static void* library(){static void*p=[](){const char*s=getenv("MYCUDSS_LIBRARY");if(!s||!*s)return (void*)nullptr;return dlopen(s,RTLD_NOW|RTLD_LOCAL|RTLD_DEEPBIND);}();return p;}
extern "C" cudssStatus_t cudssCreate(cudssHandle_t* h){NvSpan span("create",0,0);static auto f=reinterpret_cast<decltype(&cudssCreate)>(library()?dlsym(library(),"cudssCreate"):nullptr);if(!f)return CUDSS_STATUS_NOT_INITIALIZED;return f(h);}
extern "C" cudssStatus_t cudssCreateMg(cudssHandle_t* h,int n,int* dev){static auto f=reinterpret_cast<decltype(&cudssCreateMg)>(library()?dlsym(library(),"cudssCreateMg"):nullptr);if(!f)return CUDSS_STATUS_NOT_INITIALIZED;return f(h,n,dev);}
extern "C" cudssStatus_t cudssDestroy(cudssHandle_t h){static auto f=reinterpret_cast<decltype(&cudssDestroy)>(library()?dlsym(library(),"cudssDestroy"):nullptr);if(!f)return CUDSS_STATUS_NOT_INITIALIZED;return f(h);}
extern "C" cudssStatus_t cudssGetProperty(libraryPropertyType p,int* v){static auto f=reinterpret_cast<decltype(&cudssGetProperty)>(library()?dlsym(library(),"cudssGetProperty"):nullptr);if(!f)return CUDSS_STATUS_NOT_INITIALIZED;return f(p,v);}
extern "C" cudssStatus_t cudssSetStream(cudssHandle_t h,cudaStream_t t){static auto f=reinterpret_cast<decltype(&cudssSetStream)>(library()?dlsym(library(),"cudssSetStream"):nullptr);if(!f)return CUDSS_STATUS_NOT_INITIALIZED;return f(h,t);}
extern "C" cudssStatus_t cudssConfigCreate(cudssConfig_t* c){static auto f=reinterpret_cast<decltype(&cudssConfigCreate)>(library()?dlsym(library(),"cudssConfigCreate"):nullptr);if(!f)return CUDSS_STATUS_NOT_INITIALIZED;return f(c);}
extern "C" cudssStatus_t cudssConfigDestroy(cudssConfig_t c){static auto f=reinterpret_cast<decltype(&cudssConfigDestroy)>(library()?dlsym(library(),"cudssConfigDestroy"):nullptr);if(!f)return CUDSS_STATUS_NOT_INITIALIZED;return f(c);}
extern "C" cudssStatus_t cudssConfigSet(cudssConfig_t c,cudssConfigParam_t p,void* v,size_t n){static auto f=reinterpret_cast<decltype(&cudssConfigSet)>(library()?dlsym(library(),"cudssConfigSet"):nullptr);if(!f)return CUDSS_STATUS_NOT_INITIALIZED;return f(c,p,v,n);}
extern "C" cudssStatus_t cudssDataCreate(cudssHandle_t h,cudssData_t* d){NvSpan span("data_create",0,0);static auto f=reinterpret_cast<decltype(&cudssDataCreate)>(library()?dlsym(library(),"cudssDataCreate"):nullptr);if(!f)return CUDSS_STATUS_NOT_INITIALIZED;return f(h,d);}
extern "C" cudssStatus_t cudssDataDestroy(cudssHandle_t h,cudssData_t d){static auto f=reinterpret_cast<decltype(&cudssDataDestroy)>(library()?dlsym(library(),"cudssDataDestroy"):nullptr);if(!f)return CUDSS_STATUS_NOT_INITIALIZED;return f(h,d);}
extern "C" cudssStatus_t cudssDataSet(cudssHandle_t h,cudssData_t d,cudssDataParam_t p,void* v,size_t n){static auto f=reinterpret_cast<decltype(&cudssDataSet)>(library()?dlsym(library(),"cudssDataSet"):nullptr);if(!f)return CUDSS_STATUS_NOT_INITIALIZED;return f(h,d,p,v,n);}
extern "C" cudssStatus_t cudssDataGet(cudssHandle_t h,cudssData_t d,cudssDataParam_t p,void* v,size_t n,size_t* written){static auto f=reinterpret_cast<decltype(&cudssDataGet)>(library()?dlsym(library(),"cudssDataGet"):nullptr);if(!f)return CUDSS_STATUS_NOT_INITIALIZED;return f(h,d,p,v,n,written);}
extern "C" cudssStatus_t cudssMatrixCreateCsr(cudssMatrix_t* m,int64_t nr,int64_t nc,int64_t nz,void* rs,void* re,void* ci,void* v,cudaDataType_t it,cudaDataType_t vt,cudssMatrixType_t mt,cudssMatrixViewType_t mv,cudssIndexBase_t base){static auto f=reinterpret_cast<decltype(&cudssMatrixCreateCsr)>(library()?dlsym(library(),"cudssMatrixCreateCsr"):nullptr);if(!f)return CUDSS_STATUS_NOT_INITIALIZED;return f(m,nr,nc,nz,rs,re,ci,v,it,vt,mt,mv,base);}
extern "C" cudssStatus_t cudssMatrixCreateDn(cudssMatrix_t* m,int64_t nr,int64_t nc,int64_t ld,void* v,cudaDataType_t vt,cudssLayout_t layout){static auto f=reinterpret_cast<decltype(&cudssMatrixCreateDn)>(library()?dlsym(library(),"cudssMatrixCreateDn"):nullptr);if(!f)return CUDSS_STATUS_NOT_INITIALIZED;return f(m,nr,nc,ld,v,vt,layout);}
extern "C" cudssStatus_t cudssMatrixDestroy(cudssMatrix_t m){static auto f=reinterpret_cast<decltype(&cudssMatrixDestroy)>(library()?dlsym(library(),"cudssMatrixDestroy"):nullptr);if(!f)return CUDSS_STATUS_NOT_INITIALIZED;return f(m);}
extern "C" cudssStatus_t cudssExecute(cudssHandle_t h,int p,cudssConfig_t c,cudssData_t d,cudssMatrix_t a,cudssMatrix_t x,cudssMatrix_t b){NvSpan span("execute",p,(uint64_t)(uintptr_t)d);static auto f=reinterpret_cast<decltype(&cudssExecute)>(library()?dlsym(library(),"cudssExecute"):nullptr);if(!f)return CUDSS_STATUS_NOT_INITIALIZED;return f(h,p,c,d,a,x,b);}
