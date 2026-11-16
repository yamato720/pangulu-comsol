#ifndef PGL_COMSOL_BACKEND_H
#define PGL_COMSOL_BACKEND_H
#include "protocol.h"
#ifdef __cplusplus
extern "C" {
#endif
typedef struct PanguBackend PanguBackend;
PanguBackend *pc_backend_create(void);
int pc_backend_dispatch(PanguBackend *, const BridgeRequest *, BridgeResponse *, void *);
void pc_backend_destroy(PanguBackend *);
#ifdef __cplusplus
}
#endif
#endif
