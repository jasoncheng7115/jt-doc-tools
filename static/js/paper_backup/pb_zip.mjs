// 最小的 ZIP 打包（只做「不壓縮」）：把主文件與分片的 PDF 包成一個檔，全部在瀏覽器裡。
// PDF 本身已經壓縮過，再壓也小不了多少；不壓縮的格式最單純，任何解壓縮程式都打得開。
import { crc32, concat } from './pb_codec.mjs';

const te = new TextEncoder();
function u16(v) { return new Uint8Array([v & 0xff, (v >>> 8) & 0xff]); }
function u32(v) { return new Uint8Array([v & 0xff, (v >>> 8) & 0xff, (v >>> 16) & 0xff, (v >>> 24) & 0xff]); }
function dosTime(d) {
  const t = (d.getHours() << 11) | (d.getMinutes() << 5) | Math.floor(d.getSeconds() / 2);
  const day = ((d.getFullYear() - 1980) << 9) | ((d.getMonth() + 1) << 5) | d.getDate();
  return [t, day];
}

// files：[{ name, data: Uint8Array }] → Uint8Array（ZIP）
export function makeZip(files, when = new Date()) {
  const [t, day] = dosTime(when);
  const locals = [], central = [];
  let off = 0;
  for (const f of files) {
    const name = te.encode(f.name);
    const crc = crc32(f.data), size = f.data.length;
    // 0x0800 = 檔名是 UTF-8
    const head = concat(u32(0x04034b50), u16(20), u16(0x0800), u16(0), u16(t), u16(day), u32(crc), u32(size), u32(size), u16(name.length), u16(0), name);
    locals.push(head, f.data);
    central.push(concat(u32(0x02014b50), u16(20), u16(20), u16(0x0800), u16(0), u16(t), u16(day), u32(crc), u32(size), u32(size),
                        u16(name.length), u16(0), u16(0), u16(0), u16(0), u32(0), u32(off), name));
    off += head.length + size;
  }
  const cd = concat(...central);
  const end = concat(u32(0x06054b50), u16(0), u16(0), u16(files.length), u16(files.length), u32(cd.length), u32(off), u16(0));
  return concat(...locals, cd, end);
}
