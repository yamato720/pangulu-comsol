/* Model an SDK which late-loads another GNU OpenMP component. */
#include <dlfcn.h>
#include <omp.h>
extern "C" int run_nested(const char *path,int *copies,int *bodies,void **provider){
 if(omp_get_max_threads()<1)return 99;
 void *plugin=dlopen(path,RTLD_NOW|RTLD_LOCAL);if(!plugin)return 98;
 auto run=(int(*)(int*,int*))dlsym(plugin,"run_tasks");
 auto bound=(void*(*)())dlsym(plugin,"bound_task");
 if(!run||!bound)return 97;
 *provider=bound();return run(copies,bodies);
}
