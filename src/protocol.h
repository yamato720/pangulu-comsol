#ifndef PGL_CUDSS_BRIDGE_PROTOCOL_H
#define PGL_CUDSS_BRIDGE_PROTOCOL_H
#include <stdint.h>
#include <sys/socket.h>
#include <string.h>
#include <unistd.h>
#define PGL_BRIDGE_MAGIC UINT64_C(0x50474c4355445332)
enum { BR_ANALYZE=1,BR_FACTOR=2,BR_SOLVE=3,BR_CLOSE=4 };
/* All arrays in the shared mapping are normalized host CSR: uint64 offsets,
 * int32 indices, double/complex128 values, followed by dense b and x. */
typedef struct {
 uint64_t magic,command,n,nnz,nrhs,value_type,total_bytes,ptr_offset,idx_offset,val_offset,b_offset,x_offset,structure_epoch;
 int32_t device,reuse,matrix_type,matrix_view;
} BridgeRequest;
typedef struct {
 uint64_t magic;int32_t status,pangulu_status,cuda_status,stage;
 uint64_t free_bytes,total_bytes,n,nnz;
 double analysis_seconds,factor_seconds,solve_seconds;
 double conversion_seconds,value_update_seconds,pattern_seconds,release_seconds;
 uint64_t free_before_bytes,canonical_nnz;
} BridgeResponse;
static inline int bridge_send(int socket,const void *data,size_t bytes,int fd){
 struct iovec iov={(void*)data,bytes};struct msghdr msg;memset(&msg,0,sizeof(msg));msg.msg_iov=&iov;msg.msg_iovlen=1;
 char control[CMSG_SPACE(sizeof(int))];
 if(fd>=0){memset(control,0,sizeof(control));msg.msg_control=control;msg.msg_controllen=sizeof(control);struct cmsghdr *c=CMSG_FIRSTHDR(&msg);c->cmsg_level=SOL_SOCKET;c->cmsg_type=SCM_RIGHTS;c->cmsg_len=CMSG_LEN(sizeof(int));memcpy(CMSG_DATA(c),&fd,sizeof(fd));}
 return sendmsg(socket,&msg,MSG_NOSIGNAL)==(ssize_t)bytes?0:-1;
}
static inline int bridge_receive(int socket,void *data,size_t bytes,int *fd){
 struct iovec iov={data,bytes};struct msghdr msg;memset(&msg,0,sizeof(msg));msg.msg_iov=&iov;msg.msg_iovlen=1;
 char control[CMSG_SPACE(sizeof(int))];memset(control,0,sizeof(control));msg.msg_control=control;msg.msg_controllen=sizeof(control);*fd=-1;
 ssize_t got=recvmsg(socket,&msg,0);if(got!=(ssize_t)bytes)return -1;
 for(struct cmsghdr *c=CMSG_FIRSTHDR(&msg);c;c=CMSG_NXTHDR(&msg,c))if(c->cmsg_level==SOL_SOCKET&&c->cmsg_type==SCM_RIGHTS&&c->cmsg_len>=CMSG_LEN(sizeof(int)))memcpy(fd,CMSG_DATA(c),sizeof(int));
 return 0;
}
#endif
