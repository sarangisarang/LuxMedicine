# The UI. Built, not `next dev`: one command should bring the whole stack up predictably,
# and a bind-mounted node_modules across a Windows host and a Linux container is the kind of
# friction that makes "just run it" stop being true. Develop natively with `npm run dev`;
# this image is for launching the stack.

FROM node:24-alpine

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Syncs public/pdf.worker.min.mjs from the installed pdfjs-dist (prebuild does this too, but
# it is spelled out here so the worker is never missing from the image).
RUN node scripts/sync-pdf-worker.mjs && npm run build

EXPOSE 3000

CMD ["npm", "run", "start"]
