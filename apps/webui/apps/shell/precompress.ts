import { readdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { promisify } from 'node:util';
import { brotliCompress, constants, gzip } from 'node:zlib';
import type { Plugin } from 'vite';

const brotli = promisify(brotliCompress);
const gz = promisify(gzip);

export default function precompress(): Plugin {
  let output: string;
  return {
    name: 'icarus-precompress',
    apply: 'build',
    configResolved(config) {
      output = config.build.outDir;
    },
    async closeBundle() {
      async function compress(directory: string): Promise<void> {
        await Promise.all(
          (await readdir(directory, { withFileTypes: true })).map(async (entry) => {
            const file = join(directory, entry.name);
            if (entry.isDirectory()) return compress(file);
            if (!/\.(?:js|css|html|svg)$/.test(file)) return;
            const source = await readFile(file);
            if (source.length < 1024) return;
            const variants = await Promise.all([
              brotli(source, { params: { [constants.BROTLI_PARAM_QUALITY]: 6 } }),
              gz(source, { level: 9 }),
            ]);
            await Promise.all(
              variants.map((buffer, index) =>
                buffer.length < source.length
                  ? writeFile(file + (index === 0 ? '.br' : '.gz'), buffer)
                  : undefined,
              ),
            );
          }),
        );
      }
      await compress(output);
    },
  };
}
