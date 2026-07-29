# Third-party notice — katgpt-rs

โปรเจกต์นี้ **duplicate แนวคิดและอัลกอริทึม** บางส่วนจาก repository `katgpt-rs` มาเขียนใหม่เป็น Python
ภายใต้ `project/katrag/` ตามข้อกำหนด R20.4 และ R20.5 กล่าวคือ

- ไม่มีการ `import` โมดูลหรือ crate ใด ๆ จาก `katgpt-rs/`
- ไม่มีการสร้าง แก้ไข หรือลบไฟล์ใด ๆ ใน `katgpt-rs/` (ปฏิบัติเป็น read-only)

## แหล่งที่มา

| รายการ | ค่า |
|--------|-----|
| Repository ต้นทาง | `katgpt-rs` (local workspace: `D:\kmitl\kmitl_ISD\katgpt-rs`) |
| วันที่คัดลอก/อ้างอิง | 2026-07-28 |
| ผู้ถือ copyright | Todsaporn Banjerdkit |
| License | MIT |

## อัลกอริทึมที่นำมาเขียนใหม่

| ไฟล์ในโปรเจกต์นี้ | อ้างอิงแนวคิดจาก | หมายเหตุ |
|-------------------|------------------|---------|
| `katrag/common/halter.py` | `crates/katgpt-core/src/gain_cost_halt.rs` (`GainCostLoopHalter::halt_decision`) | เขียนใหม่เป็น Python; เปลี่ยนสัญญาณเป็นคะแนนคุณภาพ OCR และ evidence coverage |
| `katrag/common/maxsim.py` | `crates/katgpt-types/src/simd/maxsim.rs` (`maxsim_score`, `maxsim_score_packed`) | เขียนใหม่เป็น NumPy batched matmul |
| `katrag/common/phrase_boost.py` | `crates/katgpt-pruners/src/phrase_boost.rs` | ใช้เฉพาะแนวคิด domain lexicon boost; ไม่ใช้ cache ที่ไม่มีขอบเขตของต้นทาง |
| `katrag/common/compute_path.py` | `crates/katgpt-pruners/src/percept_router.rs` (`ComputePath`) | ใช้เฉพาะแนวคิดสามเส้นทาง fast/standard/deep |

## MIT License (ฉบับเต็มของ katgpt-rs)

```text
MIT License

Copyright (c) 2026 Todsaporn Banjerdkit

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
