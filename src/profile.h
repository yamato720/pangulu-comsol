#ifndef PGL_BRIDGE_PROFILE_H
#define PGL_BRIDGE_PROFILE_H
#ifndef PGL_PROFILE
#define PGL_PROFILE 0
#endif
#if PGL_PROFILE
#include <stdint.h>
#include <time.h>
static inline uint64_t pgl_profile_ns(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return (uint64_t)t.tv_sec*1000000000+(uint64_t)t.tv_nsec;}
#define PGL_NOW() ((double)pgl_profile_ns()*1e-9)
#define PGL_TIME_ADD(dst,start) ((dst)+=PGL_NOW()-(start))
#define PGL_TIME_SET(dst,start) ((dst)=PGL_NOW()-(start))
#define PGL_DETAIL(...) __VA_ARGS__
#else
#define PGL_NOW() 0.0
#define PGL_TIME_ADD(dst,start) ((void)(start))
#define PGL_TIME_SET(dst,start) ((void)(start))
#define PGL_DETAIL(...)
#endif
#endif
