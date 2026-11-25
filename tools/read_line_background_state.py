"""Read Immortalizer preferences through CoreFoundation; never print other app IDs."""
import ctypes as C
import json


def main():
    cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    signatures = {
        'CFStringCreateWithCString': ([C.c_void_p, C.c_char_p, C.c_uint32], C.c_void_p),
        'CFPreferencesCopyAppValue': ([C.c_void_p, C.c_void_p], C.c_void_p),
        'CFGetTypeID': ([C.c_void_p], C.c_ulong),
        'CFArrayGetTypeID': ([], C.c_ulong),
        'CFStringGetTypeID': ([], C.c_ulong),
        'CFBooleanGetTypeID': ([], C.c_ulong),
        'CFArrayGetCount': ([C.c_void_p], C.c_long),
        'CFArrayGetValueAtIndex': ([C.c_void_p, C.c_long], C.c_void_p),
        'CFStringGetCString': ([C.c_void_p, C.c_void_p, C.c_long, C.c_uint32], C.c_bool),
        'CFBooleanGetValue': ([C.c_void_p], C.c_bool),
        'CFRelease': ([C.c_void_p], None),
    }
    for name, (args, result) in signatures.items():
        getattr(cf, name).argtypes = args
        getattr(cf, name).restype = result
    owned = []

    def text(value):
        pointer = cf.CFStringCreateWithCString(None, value.encode(), 0x08000100)
        if not pointer:
            raise RuntimeError('string_allocation_failed')
        owned.append(pointer)
        return pointer

    def read(key, domain):
        pointer = cf.CFPreferencesCopyAppValue(text(key), text(domain))
        if pointer:
            owned.append(pointer)
        return pointer

    try:
        selected = read('ImmortalForegroundBundleIDs', 'com.apple.springboard')
        enabled = read('isEnabled', 'com.sergy.immortalizer.prefs')
        report = {'selected_count': None, 'line_selected': None,
                  'global_enabled_preference': None}
        if selected:
            if cf.CFGetTypeID(selected) != cf.CFArrayGetTypeID():
                raise RuntimeError('unexpected_selection_type')
            count = cf.CFArrayGetCount(selected)
            matches = False
            for index in range(count):
                value = cf.CFArrayGetValueAtIndex(selected, index)
                if cf.CFGetTypeID(value) != cf.CFStringGetTypeID():
                    raise RuntimeError('unexpected_bundle_type')
                buffer = C.create_string_buffer(1024)
                if not cf.CFStringGetCString(value, buffer, len(buffer), 0x08000100):
                    raise RuntimeError('bundle_read_failed')
                matches |= buffer.value.decode() in ('jp.naver.line', 'jp.naver.line.ipad')
            report.update(selected_count=count, line_selected=matches)
        if enabled and cf.CFGetTypeID(enabled) == cf.CFBooleanGetTypeID():
            report['global_enabled_preference'] = bool(cf.CFBooleanGetValue(enabled))
        print(json.dumps(report), flush=True)
    finally:
        for pointer in reversed(owned):
            cf.CFRelease(pointer)


if __name__ == '__main__':
    main()
