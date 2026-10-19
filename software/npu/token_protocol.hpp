// Strict server-owned JSON wire format. No SDK calls or tokenizer implementation.
#pragma once
#include <cstdint>
#include <istream>
#include <string>
#include <utility>
#include <vector>

namespace token_protocol {
constexpr std::size_t kMaxPromptBytes = 65536;
constexpr std::size_t kMaxInputTokens = 32768;
constexpr std::size_t kMaxLineBytes = 524288;
constexpr std::uint32_t kVocabSize = 248320;
struct Request {
    std::string id, prompt_hex;
    std::vector<std::int32_t> ids;
    int max_new_tokens = 0;
    bool stream = false;
};
class Cursor {
    const std::string& text_;
    std::size_t at_ = 0;
public:
    explicit Cursor(const std::string& text) : text_(text) {}
    void whitespace() {
        while (at_ < text_.size() && (text_[at_] == ' ' || text_[at_] == '\t' || text_[at_] == '\r' || text_[at_] == '\n')) ++at_;
    }
    bool literal(const char* value) {
        whitespace();
        const std::string wanted(value);
        if (text_.compare(at_, wanted.size(), wanted) != 0) return false;
        at_ += wanted.size(); return true;
    }
    bool hex_string(std::string& out, std::size_t maximum) {
        if (!literal("\"")) return false;
        const auto start = at_;
        while (at_ < text_.size() && text_[at_] != '"') {
            const char c = text_[at_];
            if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')) || at_ - start >= maximum) return false;
            ++at_;
        }
        if (at_ == text_.size()) return false;
        out = text_.substr(start, at_ - start); ++at_; return true;
    }
    bool integer(std::uint32_t& out, std::uint32_t maximum) {
        whitespace();
        if (at_ == text_.size() || text_[at_] < '0' || text_[at_] > '9') return false;
        const bool zero = text_[at_] == '0';
        std::uint32_t value = 0;
        std::size_t count = 0;
        while (at_ < text_.size() && text_[at_] >= '0' && text_[at_] <= '9') {
            const auto digit = static_cast<std::uint32_t>(text_[at_] - '0');
            if ((zero && count > 0) || value > maximum / 10 || (value == maximum / 10 && digit > maximum % 10)) return false;
            value = value * 10 + digit; ++at_; ++count;
        }
        // Delimiters are consumed by the caller. This excludes 1.0, 1e2, true,
        // quoted integers, negatives, leading plus and integer overflow.
        out = value; return true;
    }
    bool tokens(std::vector<std::int32_t>& out, std::size_t maximum) {
        if (!literal("[")) return false;
        whitespace();
        if (at_ < text_.size() && text_[at_] == ']') return false;
        while (out.size() < maximum) {
            std::uint32_t value;
            if (!integer(value, kVocabSize - 1)) return false;
            out.push_back(static_cast<std::int32_t>(value));
            if (literal("]")) return true;
            if (!literal(",")) return false;
        }
        return false;
    }
    bool boolean(bool& out) {
        if (literal("true")) { out = true; return true; }
        if (literal("false")) { out = false; return true; }
        return false;
    }
    bool ended() { whitespace(); return at_ == text_.size(); }
};

inline bool integer_arg(const char* text, int minimum, int maximum, int& result) {
    if (!text) return false;
    const std::string input(text);
    Cursor cursor(input);
    std::uint32_t value;
    if (!cursor.integer(value, static_cast<std::uint32_t>(maximum)) || !cursor.ended() || value < static_cast<std::uint32_t>(minimum)) return false;
    result = static_cast<int>(value); return true;
}

inline bool parse(const std::string& line, int context, Request& result) {
    if (line.empty() || line.size() > kMaxLineBytes || context < 32 || context > int(kMaxInputTokens)) return false;
    Request value;
    Cursor c(line);
    std::uint32_t cap;
    // Exact order is an intentional internal protocol, emitted by server_stock.py.
    // Unknown/duplicate/reordered fields and trailing data are rejected.
    if (!c.literal("{") || !c.literal("\"id\"") || !c.literal(":")) return false;
    if (!c.hex_string(value.id, 12) || value.id.size() != 12) return false;
    if (!c.literal(",") || !c.literal("\"prompt_utf8_hex\"") || !c.literal(":")) return false;
    if (!c.hex_string(value.prompt_hex, 2*kMaxPromptBytes) || value.prompt_hex.empty() || value.prompt_hex.size()%2) return false;
    if (!c.literal(",") || !c.literal("\"input_ids\"") || !c.literal(":")) return false;
    if (!c.tokens(value.ids, static_cast<std::size_t>(context))) return false;
    if (!c.literal(",") || !c.literal("\"max_new_tokens\"") || !c.literal(":")) return false;
    if (!c.integer(cap, 8192) || cap == 0 || value.ids.size() + cap > static_cast<std::size_t>(context)) return false;
    if (!c.literal(",") || !c.literal("\"stream\"") || !c.literal(":")) return false;
    if (!c.boolean(value.stream) || !c.literal("}") || !c.ended()) return false;
    value.max_new_tokens = static_cast<int>(cap);
    result = std::move(value); return true;
}

// Returns 1 for a complete bounded line, 0 for EOF, -1 for oversized/truncated.
inline int read_line(std::istream& input, std::string& output) {
    output.clear(); char c;
    while (input.get(c)) {
        if (c == '\n') return 1;
        if (output.size() == kMaxLineBytes) return -1;
        output += c;
    }
    return output.empty() ? 0 : -1;
}
} // namespace token_protocol
