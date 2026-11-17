// Diagnostic executable only. The normal build neither compiles nor links this file.
#include "activity.h"
#include <cupti.h>
#include <cstdio>
#include <cstdlib>
#include <mutex>
#include <atomic>
#include <initializer_list>
#include <unistd.h>
#include <time.h>

static uint64_t CUPTIAPI monotonic_stamp(void) { timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return (uint64_t)t.tv_sec*1000000000+t.tv_nsec; }
static FILE *output;
static std::mutex output_mutex;
static std::atomic<uint64_t> records{0}, dropped{0};
static void check(CUptiResult result, const char *operation) {
    if (result == CUPTI_SUCCESS) return;
    const char *message = nullptr;
    cuptiGetResultString(result, &message);
    fprintf(stderr,"CUPTI_ERROR operation=%s code=%d detail=%s\n",operation,(int)result,message?message:"unknown");
    exit(70);
}
static void quote(const char *s) {
    fputc('"',output);
    for (;s && *s;s++) {
        if (*s=='"' || *s=='\\') fputc('\\',output);
        if ((unsigned char)*s < 32) fprintf(output,"\\u%04x",(unsigned char)*s);
        else fputc(*s,output);
    }
    fputc('"',output);
}
static void CUPTIAPI request_buffer(uint8_t **buffer, size_t *size, size_t *maximum) {
    *size=16*1024*1024; *maximum=0;
    *buffer=(uint8_t*)aligned_alloc(8,*size);
    if (!*buffer) abort();
}
static void CUPTIAPI complete_buffer(CUcontext context,uint32_t stream,uint8_t *buffer,size_t,size_t valid) {
    std::lock_guard<std::mutex> guard(output_mutex);
    CUpti_Activity *record=nullptr;
    CUptiResult result;
    while ((result=cuptiActivityGetNextRecord(buffer,valid,&record))==CUPTI_SUCCESS) {
        records++;
        switch(record->kind) {
        case CUPTI_ACTIVITY_KIND_DRIVER:
        case CUPTI_ACTIVITY_KIND_RUNTIME: {
            auto *x=(CUpti_ActivityAPI*)record; const char *name=nullptr;
            bool runtime=record->kind==CUPTI_ACTIVITY_KIND_RUNTIME;
            cuptiGetCallbackName(runtime?CUPTI_CB_DOMAIN_RUNTIME_API:CUPTI_CB_DOMAIN_DRIVER_API,x->cbid,&name);
            fprintf(output,"{\"type\":\"%s\",\"cid\":%u,\"cbid\":%u,\"tid\":%u,\"start\":%llu,\"end\":%llu,\"result\":%u,\"name\":",runtime?"runtime":"driver",x->correlationId,x->cbid,x->threadId,(unsigned long long)x->start,(unsigned long long)x->end,x->returnValue);
            quote(name);fputs("}\n",output);break;
        }
        case CUPTI_ACTIVITY_KIND_CONCURRENT_KERNEL: {
            auto *x=(CUpti_ActivityKernel9*)record;
            fprintf(output,"{\"type\":\"kernel\",\"cid\":%u,\"stream\":%u,\"start\":%llu,\"end\":%llu,\"grid\":[%u,%u,%u],\"block\":[%u,%u,%u],\"name\":",x->correlationId,x->streamId,(unsigned long long)x->start,(unsigned long long)x->end,x->gridX,x->gridY,x->gridZ,x->blockX,x->blockY,x->blockZ);
            quote(x->name);fputs("}\n",output);break;
        }
        case CUPTI_ACTIVITY_KIND_MEMCPY: {
            auto *x=(CUpti_ActivityMemcpy6*)record;
            fprintf(output,"{\"type\":\"memcpy\",\"cid\":%u,\"runtime_cid\":%u,\"stream\":%u,\"kind\":%u,\"bytes\":%llu,\"start\":%llu,\"end\":%llu}\n",x->correlationId,x->runtimeCorrelationId,x->streamId,x->copyKind,(unsigned long long)x->bytes,(unsigned long long)x->start,(unsigned long long)x->end);break;
        }
        case CUPTI_ACTIVITY_KIND_MEMSET: {
            auto *x=(CUpti_ActivityMemset4*)record;
            fprintf(output,"{\"type\":\"memset\",\"cid\":%u,\"stream\":%u,\"bytes\":%llu,\"start\":%llu,\"end\":%llu}\n",x->correlationId,x->streamId,(unsigned long long)x->bytes,(unsigned long long)x->start,(unsigned long long)x->end);break;
        }
        case CUPTI_ACTIVITY_KIND_EXTERNAL_CORRELATION: {
            auto *x=(CUpti_ActivityExternalCorrelation*)record;
            fprintf(output,"{\"type\":\"correlation\",\"cid\":%u,\"external\":%llu}\n",x->correlationId,(unsigned long long)x->externalId);break;
        }
        default:break;
        }
    }
    if(result!=CUPTI_ERROR_MAX_LIMIT_REACHED) check(result,"next_record");
    size_t count=0;check(cuptiActivityGetNumDroppedRecords(context,stream,&count),"dropped_records");
    dropped+=count;free(buffer);
}
extern "C" void audit_activity_init(void) {
    char filename[4096]; const char *directory=getenv("PC_TRACE_DIR");
    if (!directory) {fprintf(stderr,"PC_TRACE_DIR missing\n");exit(70);}
    snprintf(filename,sizeof(filename),"%s/activities-%d.jsonl",directory,getpid());
    const char *path=filename;
    if(!path || !(output=fopen(path,"w"))) {perror("PGL_CUPTI_OUTPUT");exit(70);}
    check(cuptiActivityRegisterTimestampCallback(monotonic_stamp),"timestamp_callback");
    check(cuptiActivityRegisterCallbacks(request_buffer,complete_buffer),"callbacks");
    timespec a,b; uint64_t stamp; clock_gettime(CLOCK_MONOTONIC,&a);stamp=monotonic_stamp();clock_gettime(CLOCK_MONOTONIC,&b);
    fprintf(output,"{\"type\":\"clock\",\"host_before\":%llu,\"host_after\":%llu,\"cupti_ns\":%llu}\n",(unsigned long long)a.tv_sec*1000000000+a.tv_nsec,(unsigned long long)b.tv_sec*1000000000+b.tv_nsec,(unsigned long long)stamp);
    for(auto kind:{CUPTI_ACTIVITY_KIND_RUNTIME,CUPTI_ACTIVITY_KIND_DRIVER,CUPTI_ACTIVITY_KIND_CONCURRENT_KERNEL,CUPTI_ACTIVITY_KIND_MEMCPY,CUPTI_ACTIVITY_KIND_MEMSET,CUPTI_ACTIVITY_KIND_EXTERNAL_CORRELATION})
        check(cuptiActivityEnable(kind),"enable");
}
extern "C" void audit_activity_begin(uint64_t id) {
    check(cuptiActivityPushExternalCorrelationId(CUPTI_EXTERNAL_CORRELATION_KIND_CUSTOM0,id),"push");
    uint64_t timestamp;timestamp=monotonic_stamp();
    std::lock_guard<std::mutex> guard(output_mutex);
    fprintf(output,"{\"type\":\"scope_begin\",\"external\":%llu,\"timestamp\":%llu}\n",(unsigned long long)id,(unsigned long long)timestamp);
}
extern "C" void audit_activity_end(void) {
    uint64_t id,timestamp;timestamp=monotonic_stamp();check(cuptiActivityPopExternalCorrelationId(CUPTI_EXTERNAL_CORRELATION_KIND_CUSTOM0,&id),"pop");
    std::lock_guard<std::mutex> guard(output_mutex);
    fprintf(output,"{\"type\":\"scope_end\",\"external\":%llu,\"timestamp\":%llu}\n",(unsigned long long)id,(unsigned long long)timestamp);
}
extern "C" void audit_activity_finish(void) {
    check(cuptiActivityFlushAll(0),"flush");
    check(cuptiFinalize(),"finalize");
    fprintf(output,"{\"type\":\"summary\",\"records\":%llu,\"dropped\":%llu}\n",(unsigned long long)records.load(),(unsigned long long)dropped.load());
    fflush(output);fclose(output);output=nullptr;
    if(dropped) {fprintf(stderr,"CUPTI_DROPPED_RECORDS %llu\n",(unsigned long long)dropped.load());exit(71);}
}
