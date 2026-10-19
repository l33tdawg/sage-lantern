// Compile against the pinned public header. Fake SDK, no hardware or inference.
#define main probe_program_main
#include "../../lantern/benchmarks/tool_routing/rkllm_intent_probe.cpp"
#undef main
#include <cassert>

extern "C" RKLLMParam rkllm_createDefaultParam() { return {}; }
extern "C" int rkllm_init(LLMHandle*,RKLLMParam*,RKLLMCallback*) { return -1; }
extern "C" int rkllm_destroy(LLMHandle) { return 0; }
extern "C" int rkllm_set_chat_template(LLMHandle,const char*,const char*,const char*) { return 0; }
extern "C" int rkllm_run(LLMHandle,RKLLMInput*,RKLLMInferParam*,void*) { return -1; }

int main() {
    // Last prompt position, not first; probability mass includes disallowed IDs.
    const float logits[] = {99,0,0,0,0,0,0, 0,1,2,3,4,5,6};
    RKLLMResult result{};
    result.logits.logits=logits;result.logits.vocab_size=7;result.logits.num_tokens=2;
    Probe p;p.ids={0,1,2,3,4};
    assert(callback(&result,&p,RKLLM_RUN_NORMAL)==2);
    assert(!p.failed && p.logits.size()==5 && p.logits[4]==4);
    assert(p.label_mass>0 && p.label_mass<0.5);
    assert(callback(&result,&p,RKLLM_RUN_NORMAL)==2 && p.failed);
    Probe missing;missing.ids={0,1,2,3,99};
    assert(callback(&result,&missing,RKLLM_RUN_NORMAL)==2 && missing.failed);
    Probe empty;empty.ids={0,1,2,3,4};result.logits.vocab_size=0;
    assert(callback(&result,&empty,RKLLM_RUN_NORMAL)==2 && empty.failed);
    Probe nan;nan.ids={0,1,2,3,4};
    const float bad[]={0,1,2,3,4,std::numeric_limits<float>::quiet_NaN()};
    result.logits.logits=bad;result.logits.vocab_size=6;result.logits.num_tokens=1;
    assert(callback(&result,&nan,RKLLM_RUN_NORMAL)==2 && nan.failed);
    Probe sdk_error;assert(callback(nullptr,&sdk_error,RKLLM_RUN_ERROR)==0 && sdk_error.failed);
    std::cout << "6 native probe boundary checks passed\n";
}
