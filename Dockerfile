# -------------------------------------------------------
# Pawtraits - Static HTML Site
# Multi-stage build for minimal image size
# -------------------------------------------------------

FROM nginx:alpine

# Remove default nginx static assets
RUN rm -rf /usr/share/nginx/html/*

# Copy static site files into nginx serving directory
COPY . /usr/share/nginx/html

# Copy custom nginx config (optional but recommended)
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Expose port 80
EXPOSE 80

# nginx runs in foreground
CMD ["nginx", "-g", "daemon off;"]
