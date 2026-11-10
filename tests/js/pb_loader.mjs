// node 測試用：把內建 shamir 模組裡的 bare import 指到同一目錄的 csprng.js（瀏覽器端用 importmap 做同一件事）。
import { pathToFileURL } from 'node:url';
import path from 'node:path';
const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..', '..');
export async function resolve(specifier, context, next) {
  if (specifier === 'shamir-secret-sharing/csprng') {
    return { url: pathToFileURL(path.join(ROOT, 'static/vendor/shamir-secret-sharing-0.0.3/csprng.js')).href, shortCircuit: true };
  }
  return next(specifier, context);
}
