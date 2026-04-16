# MCP Functional Test Report

**Generated:** 2026-04-16 23:22:31

## Test CPG

| Field | Value |
|-------|-------|
| Sample ID   | `007aa7a7dd9778e91b284c7e3a43ac4fea34718684f186ad58b85477eb05dec8` |
| CPG path    | `/workspace/cpg-out/007aa7a7dd9778e91b284c7e3a43ac4fea34718684f186ad58b85477eb05dec8` |
| Method      | `WriteTIFFImage` |
| Method ID   | `107374182400L` |
| Call ID     | `30064771122L` |
| Call code   | `TIFFClose(tiff)` |
| Called fn   | `TIFFClose` |
| Class (typeDecl) | `content.c:<global>` |
| Class ID    | `167503724545L` |

### Top Methods in CPG (by call count)

| Method | ID | Calls |
|--------|----|-------|
| `WriteTIFFImage` | `107374182400L` | 163 |
| `TIFFPrintDirectory` | `107374182403L` | 0 |
| `SetImageDepth` | `107374182407L` | 0 |
| `SyncNextImageInList` | `107374182408L` | 0 |
| `TIFFSetProperties` | `107374182410L` | 0 |

## Tool Results

**18/18** tools produced correct output (15 PASS + 3 expected-empty)

| Tool | Status | Latency | Output / Notes |
|------|--------|---------|----------------|
| `check_connection` | ✓ PASS | 232ms | Successfully connected to Joern MCP, joern server version is 4.0.517 |
| `get_help` | ✓ PASS | 25ms | val res118: Helper = Welcome to the interactive help system. Below you find a table of all available top-level commands. |
| `ping` | ✓ PASS | 224ms | 4.0.517 |
| `load_cpg` | ✓ PASS | 267ms | true |
| `get_method_callees` | ✓ PASS | 232ms | ["method_full_name=assert|method_name=assert|method_signature=|method_id=107374182459L","method_full_name=<operator>.ass |
| `get_method_callers` | ~ EXPECTED_EMPTY | 235ms | top-level C functions are not called within single-file CPG |
| `get_method_code_by_full_name` | ✓ PASS | 23ms | static MagickBooleanType WriteTIFFImage(const ImageInfo *image_info,   Image *image) {   const char     *mode,     *opti |
| `get_calls_in_method_by_method_full_name` | ✓ PASS | 30ms | ["call_code=tiff=TIFFClientOpen(image->filename,mode,(thandle_t) image,TIFFReadBlob,\n    TIFFWriteBlob,TIFFSeekBlob,TIF |
| `get_method_full_name_by_id` | ✓ PASS | 261ms | WriteTIFFImage |
| `get_method_code_by_id` | ✓ PASS | 43ms | static MagickBooleanType WriteTIFFImage(const ImageInfo *image_info,   Image *image) {   const char     *mode,     *opti |
| `get_call_code_by_id` | ✓ PASS | 224ms | TIFFClose(tiff) |
| `get_method_by_call_id` | ✓ PASS | 227ms | method_full_name=WriteTIFFImage|method_name=WriteTIFFImage|method_signature=MagickBooleanType(ImageInfo*,Image*)|method_ |
| `get_referenced_method_full_name_by_call_id` | ✓ PASS | 228ms | TIFFClose |
| `get_class_full_name_by_id` | ✓ PASS | 227ms | content.c:<global> |
| `get_class_methods_by_class_full_name` | ✓ PASS | 232ms | ["methodFullName=content.c:<global> methodId=107374182401L","methodFullName=TIFFPrintDirectory methodId=107374182403L"] |
| `get_method_code_by_class_full_name_and_method_name` | ✓ PASS | 231ms | ["methodFullName=content.c:<global> methodId=107374182401L"] |
| `get_derived_classes_by_class_full_name` | ~ EXPECTED_EMPTY | 228ms | C has no class inheritance (expected empty for .c files) |
| `get_parent_classes_by_class_full_name` | ~ EXPECTED_EMPTY | 231ms | C has no class inheritance (expected empty for .c files) |

## Summary

| Status | Count | Meaning |
|--------|-------|---------|
| ✓ PASS           | 15 | Tool returned meaningful data |
| ~ EXPECTED_EMPTY | 3 | Empty is correct (e.g. no inheritance in C) |
| ○ EMPTY          | 0 | Tool ran but returned nothing - see debug |
| ✗ FAIL           | 0 | Exception / server error |
| - SKIP           | 0 | Required node ID not available |
