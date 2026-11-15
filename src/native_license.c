#include <pangulu.h>
#include <cuda_runtime_api.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int main(void){
 char message[2048]={0};int code=pangulu_license_check(message,sizeof(message));
 printf("NATIVE_LICENSE_CHECK status=%d message=%s\n",code,message);fflush(stdout);
 if(code)return 2;
 pangulu_handle_t handle=NULL;const char *idx=getenv("PC_LICENSED_GPU_INDEX");
 int rc=pangulu_create(idx?atoi(idx):0,&handle);
 if(rc){pangulu_license_status(message,sizeof(message));fprintf(stderr,"PANGULU_CREATE status=%d message=%s\n",rc,message);return 3;}
 int device=-1,runtime=0;struct cudaDeviceProp props;
 cudaGetDevice(&device);cudaGetDeviceProperties(&props,device);cudaRuntimeGetVersion(&runtime);
 printf("NATIVE_DEVICE ordinal=%d name=%s runtime=%d\n",device,props.name,runtime);
 FILE *maps=fopen("/proc/self/maps","r");char line[8192];
 if(maps){while(fgets(line,sizeof(line),maps))if(strstr(line,"libpangulu")||strstr(line,"libcublas")||strstr(line,"libcudart")){char *p=strchr(line,'/');if(p)printf("LOADED %s",p);}fclose(maps);}
 pangulu_destroy(handle);puts("NATIVE_LICENSE_OK");return 0;
}
