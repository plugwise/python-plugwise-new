#!/bin/bash

if [ ! $# -eq 1 ]; then
	echo "Syntax: $0 <directory-to-renumber"
	exit 1
fi

if [ ! -d "./${1}" ]; then
	echo "Directory ${1} not found, bailing out"
	exit 2
fi

domain_objects="./${1}/core.domain_objects.xml"

if [ ! -f "${domain_objects}" ]; then
	echo "Directory ${1} has no domain_objects, unable to continue"
	exit 3
fi

modules="./${1}/core.modules.xml"
if [ ! -f "${modules}" ]; then
	echo "Directory has no modules, only changing domain_objects"
	modules=""
fi

if [ ! "$(grep -c "0123456789AB" "${domain_objects}")" -gt 0 ]; then
	echo "File domain_objects, has no unnumbered mac-placeholders, bailing out"
	exit 4
fi

if [ "$(grep -c -E "012345670[A01]" "${domain_objects}")" -gt 0 ]; then
	echo "File domain_objects, already has numbered mac-placeholders, bailing out"
	echo ""
	echo "Checking for strays out of curtasy"
	if [ "$(grep -c -EB1 "0123456789AB" "${domain_objects}" "${modules}")" -gt 1 ]; then
		echo "Strays detected: (dumping found items)"
		grep -EB1 "0123456789AB" "${domain_objects}" "${modules}"
	fi


	exit 4
fi

# Replacements
mac_coord="012345670101"
mac_node_pre="012345670A"
mac_lan="012345670001"
#mac_wlan="012345670002"

# Scan domain_objects for mapping
echo ""
echo "Searching for Zigbee coordinator:"
coord=$(grep -EB1 "0123456789AB" "${domain_objects}" | grep -E "<zig_bee_coord" | cut -f 2 -d "'")
coordcount=$(echo "${coord}" | wc -l)

if [ "${coordcount}" -gt 1 ]; then
	echo "More than 1 coordinator node found, bailing out"
	exit 99
fi
echo " - Found coordinator at ${coord}"

echo ""
echo "Searching for Zigbee nodes:"
nodes=$(grep -EB1 "0123456789AB" "${domain_objects}" | grep -E "<(zig_bee_node|network_router)" | cut -f 2 -d "'")
nodecount=$(echo "${nodes}" | wc -l)

if [ "${nodecount}" -gt 98 ]; then
	echo "More than 98 nodes found, bailing out (not handling hex-counting yet)"
	exit 98
fi

echo " - Found ${nodecount} nodes"

echo ""
echo "Changing coordinator!"
# Coordinator change
# shellcheck disable=2086
sed -i".sedbck" '/'${coord}'/{n;s#0123456789AB#'${mac_coord}'#;}' "${domain_objects}" "${modules}"

echo ""
echo "Changing nodes!"
count=0
for node_id in ${nodes}; do
	count=$((count+1))
        counter=${count}
	echo " - Changing node ${counter}"
        if [ ${counter} -lt 10 ]; then
     		counter="0${counter}"
        fi
	# shellcheck disable=2086
	sed -i".sedbck" '/'${node_id}'/{n;s#0123456789AB#'${mac_node_pre}${counter}'#;}' "${domain_objects}" "${modules}"
done

echo ""
echo "Checking for leftover macs (except expected gateway mac)"
if [ "$(grep -c "0123456789AB" "${domain_objects}" )" -gt 1 ]; then
	echo "Unable to change gateway mac for ${domain_objects} => do this manually"
else 
	echo "Modifying main network address (assuming LAN)"
	sed -i".sedbck" 's#0123456789AB#'${mac_lan}'#' "${domain_objects}"
fi

echo ""
echo "Checking for strays"
domaincount=$(grep -v "neighbor" "${domain_objects}" | grep -c -EB1 "0123456789AB")
modulecount=$(grep -v "neighbor" "${modules}" | grep -c -EB1 "0123456789AB")
totalcount=$((domaincount+modulecount))
if [ "${totalcount}" -gt 1 ]; then
	echo "Strays detected: (dumping found items)"
	grep -EB1 "0123456789AB" "${domain_objects}" "${modules}" | grep -v "neighbor"
else
	echo "All ok"
fi


