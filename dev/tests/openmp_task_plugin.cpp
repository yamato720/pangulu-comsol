/* GNU OpenMP plugin: the if(0) task must still copy-construct firstprivate. */
#include <atomic>
#include <cstdint>
static std::atomic<int> copies{0},bodies{0},failures{0};
static constexpr uint64_t expected=UINT64_C(0x50474c5441534b31);
struct Payload {
 uint64_t value=expected;
 Payload()=default;
 Payload(const Payload& other):value(other.value){copies.fetch_add(1);}
};
extern "C" void task_symbol() asm("GOMP_task");
extern "C" void *bound_task(){return (void*)&task_symbol;}
extern "C" int run_tasks(int *ncopy,int *nbody){
 #pragma omp parallel num_threads(2)
 #pragma omp single
 {
  Payload p;
  #pragma omp task firstprivate(p) if(0)
  {bodies.fetch_add(1);if(p.value!=expected)failures.fetch_add(1);}
 }
 *ncopy=copies.load();*nbody=bodies.load();return failures.load();
}
