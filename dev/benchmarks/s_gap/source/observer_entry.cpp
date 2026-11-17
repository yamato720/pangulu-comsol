#include "activity.h"
#include <unistd.h>
#include <cstring>
#include <cstdlib>
static bool active;
static void finish(){if(active){active=false;audit_activity_finish();}}
__attribute__((constructor)) static void begin(){
 const char*enable=getenv("PC_AUDIT_CUPTI");if(!enable||strcmp(enable,"1"))return;
 char path[4096];ssize_t n=readlink("/proc/self/exe",path,sizeof(path)-1);if(n<0)return;path[n]=0;
 const char *name=strrchr(path,'/');if(!name||strcmp(name+1,"comsollauncher"))return;
 audit_activity_init();active=true;atexit(finish);
}
