#define _GNU_SOURCE
#include "backend.h"
#include <sys/mman.h>
#include <sys/stat.h>
#include <stdlib.h>

/* The worker only owns IPC. Both execution modes use the same backend. */
int main(int argc,char **argv){
 if(argc!=2)return 2;
 int socket=atoi(argv[1]);PanguBackend *state=pc_backend_create();if(!state)return 2;
 for(;;){
  BridgeRequest request;BridgeResponse response={.magic=PGL_BRIDGE_MAGIC};int fd=-1;void *memory=MAP_FAILED;
  if(bridge_receive(socket,&request,sizeof(request),&fd))break;
  if(request.magic!=PGL_BRIDGE_MAGIC){if(fd>=0)close(fd);break;}
  if(request.command==BR_CLOSE){pc_backend_dispatch(state,&request,&response,NULL);bridge_send(socket,&response,sizeof(response),-1);if(fd>=0)close(fd);break;}
  if(request.command==BR_INITIALIZE){pc_backend_dispatch(state,&request,&response,NULL);if(fd>=0)close(fd);if(bridge_send(socket,&response,sizeof(response),-1))break;continue;}
  struct stat st;
  if(request.memory_space==BR_MEMORY_IPC)pc_backend_dispatch(state,&request,&response,NULL);
  else if(request.memory_space!=BR_MEMORY_HOST)response.status=3;
  else if(fd<0||fstat(fd,&st)||!request.total_bytes||request.total_bytes>(uint64_t)st.st_size||request.total_bytes>UINT64_C(20)*1024*1024*1024)response.status=3;
  else{
   memory=mmap(NULL,(size_t)request.total_bytes,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);
   if(memory==MAP_FAILED)response.status=2;
   else pc_backend_dispatch(state,&request,&response,memory);
  }
  if(memory!=MAP_FAILED)munmap(memory,(size_t)request.total_bytes);
  if(fd>=0)close(fd);
  if(bridge_send(socket,&response,sizeof(response),-1))break;
 }
 pc_backend_destroy(state);close(socket);return 0;
}
