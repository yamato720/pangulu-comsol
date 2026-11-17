#include <cuda_runtime_api.h>
#include <cstdio>
#include <cstdlib>
#include <spawn.h>
#include <sys/wait.h>
#include <unistd.h>
extern char **environ;
static int check(cudaError_t e,const char*name){printf("IPC_API operation=%s code=%d message=%s\n",name,(int)e,cudaGetErrorString(e));fflush(stdout);return (int)e;}
int main(int argc,char**argv){if(argc!=4)return 2;cudaIpcMemHandle_t handle{};void*memory=nullptr;size_t bytes=strtoull(argv[3],nullptr,10);if(check(cudaSetDevice(0),"set_device"))return 1;
 if(argv[1][0]=='c'){FILE*f=fopen(argv[2],"rb");if(!f)return 2;if(fread(&handle,1,sizeof(handle),f)!=sizeof(handle))return 2;fclose(f);if(check(cudaIpcOpenMemHandle(&memory,handle,cudaIpcMemLazyEnablePeerAccess),"open_memory"))return 1;int value=0;if(check(cudaMemcpy(&value,memory,sizeof(value),cudaMemcpyDeviceToHost),"copy_child"))return 1;printf("IPC_VALUE %d\n",value);if(check(cudaIpcCloseMemHandle(memory),"close_memory"))return 1;return value==12345?0:1;}
 if(check(cudaMalloc(&memory,bytes),"allocate"))return 1;int value=12345;if(check(cudaMemcpy(memory,&value,sizeof(value),cudaMemcpyHostToDevice),"copy_parent"))return 1;if(check(cudaIpcGetMemHandle(&handle,memory),"export_memory"))return 1;FILE*f=fopen(argv[2],"wb");if(!f)return 2;fwrite(&handle,1,sizeof(handle),f);fclose(f);char child[]="child";char*args[]={argv[0],child,argv[2],argv[3],nullptr};pid_t pid=0;int status=0;if(posix_spawn(&pid,argv[0],nullptr,nullptr,args,environ))return 1;waitpid(pid,&status,0);check(cudaFree(memory),"free");printf("IPC_CHILD_STATUS %d\n",status);return WIFEXITED(status)?WEXITSTATUS(status):1;
}
