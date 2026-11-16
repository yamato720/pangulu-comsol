/* CPU-only loader regression. Run local and deep in separate processes. */
#include <dlfcn.h>
#include <cstdio>
#include <cstring>
int main(int argc,char **argv){
 if((argc!=4&&argc!=5)||(strcmp(argv[3],"local")&&strcmp(argv[3],"deep"))){fprintf(stderr,"foreign-openmp.so plugin.so local|deep [nested-component.so]\n");return 2;}
 void *foreign=dlopen(argv[1],RTLD_NOW|RTLD_GLOBAL);
 if(!foreign){fprintf(stderr,"foreign: %s\n",dlerror());return 2;}
 /* Both runtimes may already be mapped before the Pangu backend is opened. */
 void *gnu=dlopen("libgomp.so.1",RTLD_NOW|RTLD_GLOBAL);
 if(!gnu){fprintf(stderr,"GNU: %s\n",dlerror());return 2;}
 bool deep=strcmp(argv[3],"deep")==0;
 void *plugin=dlopen(argv[2],RTLD_NOW|RTLD_LOCAL|(deep?RTLD_DEEPBIND:0));
 if(!plugin){fprintf(stderr,"plugin: %s\n",dlerror());return 2;}
 int copies=0,bodies=0,failures=0;void *provider=nullptr;
 if(argc==5){
  auto run=(int(*)(const char*,int*,int*,void**))dlsym(plugin,"run_nested");
  if(!run){fprintf(stderr,"missing nested loader entry\n");return 2;}
  failures=run(argv[4],&copies,&bodies,&provider);
 }else{
  auto run=(int(*)(int*,int*))dlsym(plugin,"run_tasks");
  auto bound=(void*(*)())dlsym(plugin,"bound_task");
  if(!run||!bound){fprintf(stderr,"missing plugin entry\n");return 2;}
  provider=bound();failures=run(&copies,&bodies);
 }
 Dl_info info={};dladdr(provider,&info);
 printf("OMP_SCOPE mode=%s nested=%d provider=%s copies=%d bodies=%d failures=%d\n",argv[3],argc==5,info.dli_fname?info.dli_fname:"unknown",copies,bodies,failures);
 return failures||copies!=1||bodies!=1?1:0;
}
