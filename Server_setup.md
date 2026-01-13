```bash
#!/bin/bash

PROJECT_NAME="saysvenska"

# Create project folder:
mkdir $HOME/project
mkdir $HOME/project/miniconda
mkdir $HOME/project/$PROJECT_NAME-server

# Install Miniconda
echo "Installing Miniconda..."
cd $HOME
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh -b -u -p $HOME/project/miniconda
rm Miniconda3-latest-Linux-x86_64.sh
$HOME/project/miniconda/bin/conda init bash

# Install Nginx
echo "Installing Nginx..."
sudo apt update
sudo apt install -y nginx

# Install Podman
echo "Installing Podman..."
sudo apt install -y podman 

echo "Setting Podman configuration..."
podman_config_path="/home/ubuntu/.config/containers/storage.conf"

# runroot and graphroot need to be changed if your disk is full:
# Update runroot
echo "Update storage runroot"
sudo sed -i 's|^runroot\s*=\s*"/run/user/1000/containers"|runroot = "$HOME/project/docker-data/containers/storage/runroot"|' $podman_config_path
# Update graphroot
echo "Update storage graphroot"
sudo sed -i 's|^graphroot\s*=\s*"/home/ubuntu/.local/share/containers/storag"|graphroot = "$HOME/project/docker-data/containers/storage/graphroot"|' $podman_config_path

# Install Ffmpeg for handling audio
sudo apt install ffmpeg

# Install some utilities
echo "Installing utilities..."
echo "Installing htop..."
sudo apt install htop -y

# Create nginx user (if not available)
sudo adduser --system --no-create-home --shell /bin/false --group httpd

# Create tmp folder for store POST file:
# tmp should be outside user so httpd user can access
sudo mkdir $HOME/project/$PROJECT_NAME-server/tmp
sudo chown -R httpd:httpd $HOME/project/$PROJECT_NAME-server/tmp
echo "Script finished successfully."

# Install project miniconda
echo "Installing project miniconda..."
conda create -n $PROJECT_NAME python=3.12.2 -y
conda activate $PROJECT_NAME
conda install -c conda-forge transformers=4.46.3 -y
conda install -c pytorch torchaudio -y
conda install -c anaconda flask -y
conda install -c conda-forge gunicorn -y
conda install -c conda-forge python-levenshtein -y
```

If you are using Caddy, you pretty much drop nginx
```bash
#!/bin/bash

# Install Caddy
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg
sudo chmod o+r /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install caddy

# Create Caddy file
sudo tee /etc/caddy/Caddyfile >/dev/null <<'EOF'
{
	# ACME contact
	email nhan.phan@aalto.fi
}

vm4897.kaj.pouta.csc.fi {
	### Logging
	log {
		output file /var/log/caddy/access.log
		format console # or json
	}

	### Static files (equivalent to nginx root /usr/share/nginx/html)
	root * /usr/share/nginx/html
	file_server # enable static file serving

	### ───── /saysvenska/ backend ─────
	@saysvenska path /saysvenska/* # match any URI that starts with /saysvenska/
	handle @saysvenska {
		uri strip_prefix /saysvenska # rewrite /saysvenska/foo → /foo
		reverse_proxy 127.0.0.1:52705
	}

	### Optional: return 404 for anything unmatched instead of static files
	handle /* {
		respond "Not found" 404
	}
}
EOF

sudo caddy fmt --overwrite /etc/caddy/Caddyfile

sudo systemctl enable --now caddy # Should disable nginx before
journalctl -u caddy -f          # follow logs, wait for “certificate obtained”