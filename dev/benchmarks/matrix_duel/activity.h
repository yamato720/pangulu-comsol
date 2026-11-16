#ifndef PGL_AUDIT_ACTIVITY_H
#define PGL_AUDIT_ACTIVITY_H
#include <stdint.h>
#if PGL_AUDIT_CUPTI
#ifdef __cplusplus
extern "C" {
#endif
void audit_activity_init(void);
void audit_activity_begin(uint64_t id);
void audit_activity_end(void);
void audit_activity_finish(void);
#ifdef __cplusplus
}
#endif
#else
#define audit_activity_init() ((void)0)
#define audit_activity_begin(id) ((void)0)
#define audit_activity_end() ((void)0)
#define audit_activity_finish() ((void)0)
#endif
#endif
